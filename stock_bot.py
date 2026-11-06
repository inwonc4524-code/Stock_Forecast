"""
개별종목 추천 봇
- stocks.csv 에 적힌 한국·미국 대형주의 가격과 재무지표(PER·PBR·ROE·이익 성장)를 받아와서
  ① 요즘 추천 (오늘의 추천 / 쑥쑥 / 쉬는 중 / 꾸준 / 실속)
  ② 오래 꾸준히 오른 종목 (1주·1달·1·3·5·10·20년)
  을 계산하고, 곧 실적발표가 있는 종목을 표시해서
  휴대폰(ntfy)으로 요약을 보내고, docs/index.html 대시보드를 만듭니다.

실행:
  python stock_bot.py          # 실제 데이터 (GitHub Actions에서 자동 실행)
  python stock_bot.py --demo   # 가짜 데이터로 동작 확인 (인터넷 없이)

※ 이 봇의 결과는 과거 가격·재무 자료로 계산한 참고 자료일 뿐, 투자 권유가 아닙니다.
"""

import argparse
import html
import json
import math
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

KST = timezone(timedelta(hours=9))
HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(HERE, "docs")
TOP_N = 10                   # 추천 카드마다 보여줄 개수
LONG_SHOW = 50               # 순위 목록에 넣을 개수 (11위부터는 더보기)
FIRST_SHOW = 10              # 처음에 보여줄 개수
MIN_DAYS = 220               # 이보다 데이터가 짧으면(상장 1년 미만 등) 제외
EARN_SOON = 14               # 실적발표가 며칠 안으로 다가오면 📢 표시할지
EARN_ALERT = 7               # 휴대폰 알림에 '곧 실적발표'로 넣을 기간(일)
PERIODS = ["1d", "1w", "1m", 1, 3, 5, 10, 20]
PERIOD_INFO = {   # 기간: (버튼 이름, 칸 하나의 단위, 세는 말, 기간 이름)
    "1d": ("1일", "시간", "시간", "1일"), "1w": ("1주", "날", "일", "1주"), "1m": ("1달", "주", "주", "1달"),
    1: ("1년", "달", "달", "1년"), 3: ("3년", "해", "번", "3년"), 5: ("5년", "해", "번", "5년"),
    10: ("10년", "해", "번", "10년"), 20: ("20년", "해", "번", "20년"),
}

MARKETS = [("KR", "🇰🇷", "한국"), ("US", "🇺🇸", "미국")]
FLAG = {c: f for c, f, _ in MARKETS}

# 테마 (주식뉴스알림과 같은 테마) — stocks.csv 의 theme 칸에 아래 key를 적어요. 여러 개면 | 로 이어요 (예: kr|bat)
THEMES = [
    ("kr", "🇰🇷 한국 대표", "한국에서 덩치가 큰 회사들이에요"),
    ("us", "🇺🇸 미국 대표", "미국에서 덩치가 큰 회사들이에요 (S&P500 안의 큰 회사)"),
    ("bio", "🧬 나스닥 바이오", "약이나 치료법을 새로 만드는 미국 회사들이에요"),
    ("bat", "🔋 2차전지", "전기차·휴대폰에 들어가는, 다시 충전해 쓰는 배터리를 만드는 회사들이에요"),
    ("ess", "⚡ 전고체·ESS", "더 안전한 차세대 배터리(전고체)와 전기를 모아두는 큰 배터리(ESS)를 만드는 회사들이에요"),
    ("power", "🏭 AI 전력", "AI 데이터센터가 쓰는 엄청난 전기를 만들고 보내는 회사들이에요"),
    ("robot", "🤖 로봇", "로봇과 공장 자동화 기계를 만드는 회사들이에요"),
]
THEME_NAME = {k: n for k, n, _ in THEMES}


def in_theme(df, key):
    """그 테마에 들어 있는 종목만"""
    if key is None:
        return df
    return df[df.theme.fillna("").str.split("|").apply(lambda ts: key in ts)]


# ───────────────────────── 1. 데이터 받아오기 ─────────────────────────

CLOSE_KST = {"KR": "15:30", "US": "06:00", "CN": "16:00", "HK": "17:00"}   # 각 나라 장 마감 (한국시간)
MKT_NAME = {"KR": "한국", "US": "미국", "CN": "중국", "HK": "홍콩"}


def load_universe():
    u = pd.read_csv(os.path.join(HERE, "stocks.csv"), dtype=str)
    if "theme" not in u:
        u["theme"] = ""
    u["theme"] = u.theme.fillna("").str.replace(" ", "")
    return u


RAW = {}   # 실제 가격(배당 조정 안 한 종가) — '실제 가격으로 얼마 올랐나' 표시에 사용


def _pick(raw, key, name):
    """다운로드 결과에서 수익률 계산용(배당 포함 수정종가)을 돌려주고, 실제 가격은 RAW에 저장"""
    d = raw[key] if isinstance(raw.columns, pd.MultiIndex) else raw
    real = d["Close"].dropna()
    real = real[real > 0]
    adj = (d["Adj Close"] if "Adj Close" in d else d["Close"]).dropna()
    adj = adj[adj > 0]
    if len(real):
        RAW[name] = real
    return adj


INTRA = {}   # 1일: 마지막 장날의 1시간 단위 가격 {"date", "open", "close": [...], "times": [...]}


def fetch_intraday(tickers):
    """마지막 장날, 장 시작부터 마감까지 1시간마다 가격"""
    import yfinance as yf
    try:
        raw = yf.download(tickers, period="5d", interval="60m", auto_adjust=False,
                          progress=False, group_by="ticker", threads=True, prepost=False)
    except Exception as e:
        print("시간별 가격을 못 받았어요:", e)
        return
    for t in tickers:
        try:
            d = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            d = d.dropna(subset=["Open", "Close"])
            d = d[d.Close > 0]
            if d.empty:
                continue
            days = [x.date() for x in d.index]
            last = days[-1]
            day = d[[x == last for x in days]]
            INTRA[t] = {"date": pd.Timestamp(last), "open": float(day.Open.iloc[0]),
                        "close": [float(x) for x in day.Close], "times": list(day.index)}
        except Exception:
            pass
    print(f"시간별 가격을 받은 곳: {len(INTRA)}/{len(tickers)}")


def demo_intraday(closes, seed=3):
    rng = np.random.default_rng(seed)
    for t, s in closes.items():
        o = float(s.iloc[-2])
        moves = rng.normal(0, 0.006, 7)
        cl = list(o * np.exp(np.cumsum(moves)))
        start = pd.Timestamp(s.index[-1]) + pd.Timedelta(hours=9)
        INTRA[t] = {"date": pd.Timestamp(s.index[-1]), "open": o, "close": cl,
                    "times": [start + pd.Timedelta(hours=h) for h in range(7)]}


def apply_intraday(df):
    """1일 = 그 나라 장 시작 → 장 마감 (시간별 가격이 없으면 전날 마감 대비)"""
    def one(r):
        it = INTRA.get(r.ticker)
        if it and it["date"].strftime("%Y-%m-%d") == r.date:
            return it["close"][-1] / it["open"] - 1
        return r.r1d
    df["r1d"] = df.apply(one, axis=1)
    return df


def intraday_term(ticker):
    it = INTRA.get(ticker)
    if not it:
        return None
    o, cl = it["open"], it["close"]
    prev = [o] + cl[:-1]
    blocks = [c / p - 1 for c, p in zip(cl, prev)]
    labels = [f"{t:%H:%M}부터 1시간" for t in it["times"]]
    return {"total": cl[-1] / o - 1, "cagr": np.nan, "blocks": blocks, "labels": labels,
            "ups": sum(b > 0 for b in blocks), "start": it["date"], "end": it["date"],
            "p0": o, "p1": cl[-1]}


def fetch_prices(tickers):
    """야후 파이낸스에서 상장 이후 전체 기간의 가격(배당·액면분할 반영 수정종가)을 받아옵니다."""
    import yfinance as yf

    raw = yf.download(tickers, period="max", auto_adjust=False,
                      progress=False, group_by="ticker", threads=True)
    closes = {}
    for t in tickers:
        try:
            s = _pick(raw, t, t)
            s = s.dropna()
            s = s[s > 0]
            if len(s) > 0:
                closes[t] = s
        except Exception:
            pass
    # 잠깐 오류로 못 받은 종목은 하나씩 한 번 더 받아오기
    retry = [t for t in tickers if t not in closes]
    if retry:
        raw_r = yf.download(retry, period="max", auto_adjust=False,
                            progress=False, group_by="ticker", threads=False)
        for t in retry:
            try:
                s = _pick(raw_r, t, t)
                if len(s) > 0:
                    closes[t] = s
            except Exception:
                pass
    # 한국 종목은 코스피(.KS)·코스닥(.KQ)을 잘못 적었을 수 있어서 반대로 한 번 더 시도
    swap = {t: t[:-3] + (".KQ" if t.endswith(".KS") else ".KS")
            for t in tickers if t not in closes and t.endswith((".KS", ".KQ"))}
    if swap:
        raw2 = yf.download(list(swap.values()), period="max", auto_adjust=False,
                           progress=False, group_by="ticker", threads=True)
        for t, alt in swap.items():
            try:
                s = _pick(raw2, alt, t)
                s = s.dropna()
                s = s[s > 0]
                if len(s) > 0:
                    closes[t] = s
                    print(f"{t} → {alt} 로 찾았어요 (stocks.csv에서 고쳐주면 좋아요)")
            except Exception:
                pass
    missing = [t for t in tickers if t not in closes]
    if missing:
        print("가격을 못 받은 종목:", ", ".join(missing))
    return closes


def num(v):
    """숫자로 바꿀 수 없거나 무한대면 NaN"""
    try:
        v = float(v)
        return v if math.isfinite(v) else np.nan
    except (TypeError, ValueError):
        return np.nan


def _to_date(v):
    """여러 모양의 날짜를 date로. 비어 있거나(NaT/NaN) 이상하면 None."""
    try:
        if v is None or pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        if isinstance(v, (int, float)):
            d = datetime.fromtimestamp(v, KST).date()
        elif isinstance(v, datetime):
            d = v.date()
        elif isinstance(v, date):
            d = v
        else:
            ts = pd.Timestamp(v)
            d = None if pd.isna(ts) else ts.date()
        return d if type(d) is date else None
    except Exception:
        return None


def next_earnings(tk, info, today):
    """다가오는 실적발표일 (모르면 None)"""
    cands = [_to_date(info.get(k)) for k in ("earningsTimestampStart", "earningsTimestamp")]
    try:
        cal = tk.calendar
        if isinstance(cal, dict):
            ed = cal.get("Earnings Date") or []
            cands += [_to_date(d) for d in (ed if isinstance(ed, (list, tuple)) else [ed])]
    except Exception:
        pass
    future = sorted(d for d in cands if type(d) is date and d >= today)
    return future[0] if future else None


def naver_kr(ticker):
    """한국 종목의 PER·PBR을 네이버 증권에서 받아옵니다 (야후에 없을 때 대신 사용).
    값이 없거나 '적자' 등으로 숫자가 아니면 NaN."""
    code = ticker.split(".")[0]
    try:
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/integration",
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        infos = r.json().get("totalInfos") or []
    except Exception as e:
        print(f"{ticker} 네이버 자료 못 받음: {e}")
        return {}
    vals = {}
    for it in infos:
        key = str(it.get("code") or it.get("key") or "").lower()
        raw = str(it.get("value") or "").replace(",", "").replace("배", "").replace("원", "").strip()
        vals[key] = num(raw)
    return {"per": vals.get("per", np.nan), "pbr": vals.get("pbr", np.nan),
            "eps": vals.get("eps", np.nan), "_keys": list(vals)}


def fetch_fundamentals(tickers, today):
    """종목마다 PER·PBR·ROE·이익 성장·실적발표일을 받아옵니다. 못 받으면 빈칸."""
    import yfinance as yf

    def one(t):
        tk = yf.Ticker(t)
        try:
            info = tk.info or {}
        except Exception:
            info = {}
        growth = num(info.get("earningsGrowth"))
        if pd.isna(growth):
            growth = num(info.get("earningsQuarterlyGrowth"))
        price = num(info.get("currentPrice"))
        if pd.isna(price):
            price = num(info.get("regularMarketPrice"))
        per = num(info.get("trailingPE"))
        if pd.isna(per):   # 한국 종목은 PER이 비어 있는 경우가 많아서 주가 ÷ 주당순이익으로 직접 계산
            eps = num(info.get("trailingEps"))
            if not pd.isna(eps) and not pd.isna(price) and eps != 0:
                per = price / eps if eps > 0 else -1.0
        pbr = num(info.get("priceToBook"))
        if pd.isna(pbr):   # 주가 ÷ 주당순자산
            bv = num(info.get("bookValue"))
            if not pd.isna(bv) and not pd.isna(price) and bv > 0:
                pbr = price / bv
        if t.endswith((".KS", ".KQ")) and (pd.isna(per) or pd.isna(pbr)):
            nv = naver_kr(t)
            if t == "005930.KS":   # 네이버 자료가 어떻게 오는지 확인용
                print("005930 네이버:", nv)
            if pd.isna(per):
                per = nv.get("per", np.nan)
                if pd.isna(per) and nv.get("eps", np.nan) < 0:
                    per = -1.0   # 주당순이익이 마이너스 = 적자
            if pd.isna(pbr):
                pbr = nv.get("pbr", np.nan)
        return t, {
            "per": per,
            "pbr": pbr,
            "roe": num(info.get("returnOnEquity")),
            "growth": growth,
            "margin": num(info.get("profitMargins")),
            "earn": _safe_earn(tk, info, today, t),
        }

    def _safe_earn(tk, info, today, t):
        try:
            return next_earnings(tk, info, today)
        except Exception as e:   # 한 종목 문제로 전체가 멈추지 않게
            print(f"{t} 실적발표일 못 읽음: {e}")
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        out = dict(ex.map(one, tickers))
    got = sum(1 for v in out.values() if not pd.isna(v["per"]) or not pd.isna(v["roe"]))
    print(f"재무지표를 받은 종목: {got}/{len(tickers)}")
    return out


def demo_prices(tickers, seed=7):
    """인터넷 없이 테스트할 때 쓰는 가짜 가격 (종목마다 2~25년치)."""
    rng = np.random.default_rng(seed)
    closes = {}
    for t in tickers:
        years = int(rng.integers(2, 26))
        idx = pd.bdate_range(end=datetime.now(KST).date(), periods=252 * years)
        drift = rng.normal(0.0004, 0.0006)
        vol = rng.uniform(0.010, 0.035)
        r = rng.normal(drift, vol, len(idx))
        r[-25:] += rng.normal(0, 0.006)
        closes[t] = pd.Series(10000 * np.exp(np.cumsum(r)), index=idx)
    RAW.update(closes)
    return closes


def demo_fundamentals(tickers, today, seed=11):
    rng = np.random.default_rng(seed)
    out = {}
    for t in tickers:
        loss = rng.random() < 0.1
        roe = rng.normal(-0.08, 0.05) if loss else rng.uniform(0.02, 0.35)
        out[t] = {
            "per": np.nan if loss else float(rng.uniform(5, 60)),
            "pbr": float(rng.uniform(0.4, 12)),
            "roe": float(roe),
            "growth": float(rng.normal(0.08, 0.3)),
            "margin": float(roe / 2),
            "earn": today + timedelta(days=int(rng.integers(0, 90))) if rng.random() < 0.8 else None,
        }
    return out


# ───────────────────────── 2. 요즘 지표 계산 ─────────────────────────

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return float((100 - 100 / (1 + rs)).iloc[-1])


def ret(s, days):
    return float(s.iloc[-1] / s.iloc[-1 - days] - 1) if len(s) > days else np.nan


def ret_days(s, days):
    """달력으로 며칠 전 마감 가격과 비교 (1주=7일, 1달=4주=28일)"""
    start = s.index[-1] - pd.Timedelta(days=days)
    if s.index[0] > start:
        return np.nan
    return float(s.iloc[-1] / s.asof(start) - 1)


def indicators(s):
    s = s.dropna()
    last_year = s.iloc[-252:]
    ma200 = s.rolling(200).mean()
    daily = s.pct_change().dropna()
    running_max = last_year.cummax()
    return {
        "price": float(s.iloc[-1]),
        "date": s.index[-1].strftime("%Y-%m-%d"),
        "since": s.index[0].strftime("%Y-%m-%d"),
        "r1d": ret(s, 1),
        "r1w": ret_days(s, 7),
        "r1m": ret_days(s, 28),
        "r3m": ret(s, 63),
        "r6m": ret(s, 126),
        "r12m": ret(s, 252),
        "above_ma200": bool(s.iloc[-1] > ma200.iloc[-1]),
        "ma200_rising": bool(ma200.iloc[-1] > ma200.iloc[-21]),
        "rsi14": rsi(s),
        "vol60": float(daily.iloc[-60:].std() * np.sqrt(252)),
        "mdd1y": float((last_year / running_max - 1).min()),
        "from_high": float(s.iloc[-1] / last_year.max() - 1),
    }


def pct_rank(series, ascending=True):
    r = series.rank(pct=True, ascending=ascending)
    return (r * 100).round(1)


def score(df):
    """한 시장(한국 또는 미국) 안에서 서로 비교해 점수를 매깁니다."""
    df = df.copy()
    # 쑥쑥: 1·3·6개월 수익률 가중 평균, 200일 평균선 아래면 반으로 깎음
    df["mom_raw"] = 0.2 * df.r1m + 0.4 * df.r3m + 0.4 * df.r6m
    df["mom_score"] = pct_rank(df.mom_raw)
    df.loc[~df.above_ma200, "mom_score"] *= 0.5

    # 쉬는 중: 긴 흐름(200일 평균선)은 오르는 중인데 요즘 많이 내려온 것 (적자 회사는 제외)
    df["rebound_score"] = (0.5 * pct_rank(df.rsi14, ascending=False)
                           + 0.5 * pct_rank(df.from_high, ascending=False)).round(1)
    df["rebound_ok"] = (df.ma200_rising & ((df.rsi14 < 40) | (df.from_high <= -0.10))
                        & ~(df.roe < 0))

    # 천천히 꾸준히: 흔들림·최대 하락폭이 작고 6개월 수익은 플러스
    df["stab_score"] = (0.6 * pct_rank(df.vol60, ascending=False)
                        + 0.4 * pct_rank(df.mdd1y)).round(1)
    df["stab_ok"] = df.r6m > 0

    # 실속(재무): PER·PBR은 낮을수록, ROE·이익 성장은 높을수록 좋음
    loss = (df.roe < 0) | (df.per <= 0)
    per_s = pct_rank(df.per.where(df.per > 0), ascending=False)
    per_s[loss] = 10
    pbr_s = pct_rank(df.pbr.where(df.pbr > 0), ascending=False)
    roe_s = pct_rank(df.roe)
    gr_s = pct_rank(df.growth)
    parts = pd.concat([per_s, pbr_s, roe_s, gr_s], axis=1)
    df["fin_score"] = parts.mean(axis=1, skipna=True).round(1).where(parts.notna().sum(axis=1) >= 2)
    df["value_ok"] = df.fin_score.notna() & (df.roe > 0) & ~loss

    # 오늘의 추천: 쑥쑥 45% + 안정 25% + 실속 30% (재무 자료가 없으면 쑥쑥 60% + 안정 40%)
    with_fin = 0.45 * df.mom_score + 0.25 * df.stab_score + 0.30 * df.fin_score
    no_fin = 0.6 * df.mom_score + 0.4 * df.stab_score
    df["total"] = with_fin.fillna(no_fin).round(1)
    df.loc[df.rsi14 >= 75, "total"] -= 10
    df.loc[loss, "total"] -= 10
    df["overheated"] = df.rsi14 >= 70
    df["loss"] = loss
    return df


# ───────────────────────── 3. 오래 꾸준히 오른 종목 ─────────────────────────

def long_term(s, years):
    """기간 동안의 성적. 데이터가 모자라면 None.
    1주·1달: 하루씩, 1년: 12달을 한 달씩, 3년 이상: 1년씩 잘라서 몇 번 올랐는지 셉니다."""
    end = s.index[-1]
    if years == "1w":   # 달력 7일, 하루씩 (장이 쉰 날은 None)
        if len(s) < 8:
            return None
        days = [end - pd.Timedelta(days=k) for k in range(6, -1, -1)]
        blocks, labels = [], []
        for d in days:
            wd = "월화수목금토일"[d.weekday()]
            labels.append(f"{d.month}월 {d.day}일 ({wd})")
            if d in s.index:
                prev = s[s.index < d]
                blocks.append(float(s[d] / prev.iloc[-1] - 1) if len(prev) else None)
            else:
                blocks.append(None)
        start = end - pd.Timedelta(days=7)
        total = float(s.iloc[-1] / s.asof(start) - 1)
        return {"total": total, "cagr": np.nan, "blocks": blocks, "start": start, "end": end,
                "labels": labels, "ups": sum(1 for b in blocks if b is not None and b > 0)}
    if years == "1m":   # 4주, 일주일씩
        start = end - pd.Timedelta(days=28)
        if s.index[0] > start:
            return None
        cuts = [end - pd.Timedelta(days=7 * k) for k in range(4, -1, -1)]
        px = [float(s.asof(c)) for c in cuts]
        blocks = [px[i + 1] / px[i] - 1 for i in range(4)]
        labels = [f"{(a + pd.Timedelta(days=1)).month}/{(a + pd.Timedelta(days=1)).day}~{b.month}/{b.day}"
                  for a, b in zip(cuts[:-1], cuts[1:])]
        return {"total": px[-1] / px[0] - 1, "cagr": np.nan, "blocks": blocks, "start": start,
                "end": end, "labels": labels, "ups": sum(b > 0 for b in blocks)}
    start = end - pd.DateOffset(years=years)
    if s.index[0] > start + pd.Timedelta(days=15):
        return None
    if years == 1:
        cuts = [end - pd.DateOffset(months=m) for m in range(12, -1, -1)]
        labels = [(c + pd.DateOffset(days=1)).strftime("%y년 %m월") for c in cuts[1:]]
    else:
        cuts = [end - pd.DateOffset(years=y) for y in range(years, -1, -1)]
        labels = [f"{c.strftime('%Y.%m')}~{d.strftime('%Y.%m')}" for c, d in zip(cuts[:-1], cuts[1:])]
    px = [float(s.asof(c)) for c in cuts]
    blocks = [px[i + 1] / px[i] - 1 for i in range(len(px) - 1)]
    total = px[-1] / px[0] - 1
    return {
        "total": total,
        "start": cuts[0],
        "cagr": (1 + total) ** (1 / years) - 1,
        "blocks": blocks,
        "labels": labels,
        "ups": sum(b > 0 for b in blocks),
    }


def build_long(universe, closes):
    out = {}
    for y in PERIODS:
        rows = []
        for _, u in universe.iterrows():
            s = closes.get(u.ticker)
            if s is None or s.empty:
                continue
            lt = intraday_term(u.ticker) if y == "1d" else long_term(s.dropna(), y)
            if lt:
                real = RAW.get(u.ticker)
                if y != "1d" and real is not None and len(real):
                    lt["p0"] = float(real.asof(lt["start"])) if real.index[0] <= lt["start"] else np.nan
                    lt["p1"] = float(real.iloc[-1])
                rows.append({**u.to_dict(), **lt})
        out[y] = rows
    return out


def long_rank(rows):
    """올라간 횟수가 많은 순 → 같으면 1년 평균 수익률(또는 전체 수익률) 높은 순. 손해 본 건 제외."""
    good = [r for r in rows if r["total"] > 0]
    return sorted(good, key=lambda r: (r["ups"] / max(1, sum(b is not None for b in r["blocks"])),
                                       r["total"] if pd.isna(r["cagr"]) else r["cagr"]),
                  reverse=True)


# ───────────────────────── 4. 공통 ─────────────────────────

def build_table(universe, closes, funds, today):
    rows = []
    for _, u in universe.iterrows():
        s = closes.get(u.ticker)
        if s is None or len(s.dropna()) < MIN_DAYS:
            continue
        f = dict(funds.get(u.ticker, {}))
        earn = f.pop("earn", None)
        rows.append({**u.to_dict(), **indicators(s), **f,
                     "earn": earn.isoformat() if earn else "",
                     "earn_days": (earn - today).days if earn else np.nan})
    if not rows:
        raise SystemExit("계산할 수 있는 종목이 없습니다. 데이터 연결을 확인하세요.")
    df = pd.DataFrame(rows)
    for c in ("per", "pbr", "roe", "growth", "margin"):
        if c not in df:
            df[c] = np.nan
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return pd.concat([score(g) for _, g in df.groupby("market", sort=False)], ignore_index=True)


def picks(df):
    return {
        "overall": df[df.above_ma200].nlargest(TOP_N, "total"),
        "momentum": df[df.above_ma200 & (df.mom_raw > 0)].nlargest(TOP_N, "mom_score"),
        "rebound": df[df.rebound_ok].nlargest(TOP_N, "rebound_score"),
        "stable": df[df.stab_ok].nlargest(TOP_N, "stab_score"),
        "value": df[df.value_ok].nlargest(TOP_N, "fin_score"),
    }


def pct(x):
    return "-" if pd.isna(x) else f"{x * 100:+.0f}%" if abs(x) >= 0.1 else f"{x * 100:+.1f}%"


def shake(v):
    """흔들림(1년 환산 변동성)을 쉬운 말로 — 개별종목은 ETF보다 원래 많이 흔들려요"""
    if v < 0.25:
        return "작음"
    if v < 0.40:
        return "보통"
    return "큼"


def times(v):
    return "-" if pd.isna(v) or v <= 0 else f"{v:.1f}배" if v < 100 else f"{v:.0f}배"


def years_text(v):
    """PER을 '몇 년이면 본전'으로 (주가 ÷ 1년에 버는 돈)"""
    return "-" if pd.isna(v) or v <= 0 else f"{v:.1f}년" if v < 100 else f"{v:.0f}년"


def per_text(r):
    return "손해 중" if r["loss"] else years_text(r["per"])


def dday(d):
    if pd.isna(d):
        return ""
    return "오늘" if d == 0 else f"D-{int(d)}"


def earn_soon(r, days=EARN_SOON):
    return not pd.isna(r["earn_days"]) and 0 <= r["earn_days"] <= days


def short_ticker(t):
    return t.split(".")[0]


def money(v, market):
    """나라별 돈 단위로"""
    if pd.isna(v):
        return "-"
    if market == "KR":
        return f"{v:,.0f}원"
    if market == "US":
        return f"${v:,.2f}"
    if market == "CN":
        return f"{v:,.2f}위안"
    if market == "HK":
        return f"HK${v:,.2f}"
    return f"{v:,.2f}"


def load_reasons():
    """reasons.json — 기간별로 '왜 올랐나' 한 줄 (Claude가 뉴스를 보고 정리)"""
    try:
        with open(os.path.join(HERE, "reasons.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


REASONS = load_reasons()


def reason_for(ticker, years, end=None):
    r = REASONS.get(ticker) or {}
    if years == "1d":   # 1일 이유는 그 날짜 것만 (예전 날 이유가 남아 있으면 안 보여줌)
        if end is None or r.get("1d_date") != pd.Timestamp(end).strftime("%Y-%m-%d"):
            return ""
    key = years if years in ("1d", "1w", "1m") else "1" if years == 1 else "long"
    return r.get(key, "")


def won(total):
    """100만 원이 얼마가 됐는지"""
    v = 100 * (1 + total)
    return f"{v:,.0f}만 원" if v < 10000 else f"{v / 10000:.1f}억 원"


# ───────────────────────── 5. 휴대폰 알림 ─────────────────────────

def phone_message(df, long, today):
    out = [f"📅 {today} 기준"]
    for key, label, _ in THEMES:
        sub = in_theme(df, key)
        if sub.empty:
            continue
        p = picks(sub)
        out.append(f"\n{label}")
        top = p["overall"].head(2)
        if top.empty:
            out.append("🏆 오늘의 추천: 요즘 오르는 회사가 없어요 (다들 쉬는 중)")
        for i, (_, r) in enumerate(top.iterrows(), 1):
            tags = (" 🔥" if r.overheated else "") + (f" 📢성적표 {dday(r.earn_days)}" if earn_soon(r) else "")
            out.append(f"🏆{i}등 {FLAG.get(r.market, '')}{r['name']} ({r.total:.0f}점){tags}")
        if not p["momentum"].empty:
            r = p["momentum"].iloc[0]
            out.append(f"🚀 쑥쑥: {FLAG.get(r.market, '')}{r['name']} 6달 동안 {pct(r.r6m)}{' 🔥' if r.overheated else ''}")

    p = picks(df)
    out.append("\n🌐 테마 상관없이 전체에서")
    for icon, key, extra in [
        ("🎢 잠깐 쉬는 중", "rebound", lambda r: f"제일 비쌀 때보다 {pct(r.from_high)}"),
        ("🐢 천천히 꾸준히", "stable", lambda r: f"흔들림 {shake(r.vol60)}"),
        ("💎 싸고 튼튼한 회사", "value", lambda r: (f"{years_text(r.per)}이면 본전 · " if not pd.isna(r.per) else "")
                                      + f"돈 버는 실력 {pct(r.roe)}"),
    ]:
        rows = p[key]
        if rows.empty:
            out.append(f"{icon}: 없음")
        else:
            r = rows.iloc[0]
            out.append(f"{icon}: {FLAG.get(r.market, '')}{r['name']} {extra(r)}{' 🔥' if r.overheated else ''}")

    soon = df[df.apply(lambda r: earn_soon(r, EARN_ALERT), axis=1)].sort_values("earn_days")
    if not soon.empty:
        names = ", ".join(f"{FLAG.get(r.market, '')}{r['name']} {dday(r.earn_days)}"
                          for _, r in soon.head(8).iterrows())
        more = f" 외 {len(soon) - 8}개" if len(soon) > 8 else ""
        out.append(f"\n📢 일주일 안에 성적표가 나와요: {names}{more}")

    best = long_rank(long.get(10, []))
    if best:
        b = best[0]
        out.append(f"\n🌱 10년 꾸준히 1등: {FLAG.get(b['market'], '')} {b['name']}"
                   f" — 10번 중 {b['ups']}번 오름, 100만 원 → {won(b['total'])}")
    us = df[(df.market == "US") & (df.r1d > 0)].sort_values("r1d", ascending=False).head(3)
    if not us.empty:
        d = pd.Timestamp(us.date.max())
        names = ", ".join(f"{r['name']} {pct(r.r1d)}" for _, r in us.iterrows())
        out.append(f"\n🇺🇸 미국장 마감({d.month}월 {d.day}일) 하루 동안 많이 오른 곳: {names}")
    out.append("\n🔥 = 너무 빨리 올라서 조심 · 📢 = 곧 회사 성적표가 나와요(이때 가격이 크게 움직일 수 있어요)")
    out.append("※ 지나간 숫자로 계산한 참고용이에요. 꼭 오른다는 뜻도, 사라는 뜻도 아니에요.")
    return "\n".join(out)


def close_message(df, market, today):
    """그 나라 장이 끝났을 때: 오늘 하루 많이 오른 종목"""
    sub = df[df.market == market].dropna(subset=["r1d"]).sort_values("r1d", ascending=False)
    flag = FLAG.get(market, "")
    name = MKT_NAME.get(market, market)
    if sub.empty:
        return None
    last = sub.date.max()
    late = "" if last == today else f"\n(아직 오늘 가격이 안 들어와서 {last} 기준이에요)"
    up = sub[sub.r1d > 0]
    out = [f"{flag} {name} 주식시장이 끝났어요 ({CLOSE_KST.get(market, '')} 마감){late}", ""]
    if up.empty:
        out.append("오늘은 오른 곳이 없어요. 다 같이 쉬는 날이에요 🛌")
    else:
        out.append("🔝 오늘 장 시작부터 마감까지 많이 오른 순서")
        for i, (_, r) in enumerate(up.head(5).iterrows(), 1):
            out.append(f"{i}. {r['name']} {pct(r.r1d)}" + (f"  ({theme_names(r)})" if theme_names(r) else ""))
    down = sub[sub.r1d < 0].tail(1)
    if not down.empty:
        r = down.iloc[0]
        out.append(f"\n📉 가장 많이 내린 곳: {r['name']} {pct(r.r1d)}")
    n_up, n_all = int((sub.r1d > 0).sum()), len(sub)
    out.append(f"\n오른 곳 {n_up}개 / 전체 {n_all}개")
    out.append("※ 하루 움직임은 운도 커요. 참고만 하세요.")
    return "\n".join(out)


def theme_names(r):
    t = str(r.get("theme", "") or "")
    names = [THEME_NAME[k] for k in t.split("|") if k and k not in ("kr", "us") and k in globals().get("THEME_NAME", {})]
    return ", ".join(names)


def send_ntfy(title, message, click_url):
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        print("NTFY_TOPIC 이 없어서 알림은 건너뜁니다.")
        return
    payload = {"topic": topic, "title": title, "message": message,
               "tags": ["chart_with_upwards_trend"]}
    if click_url:
        payload["click"] = click_url
        payload["actions"] = [{"action": "view", "label": "대시보드 열기", "url": click_url}]
    r = requests.post("https://ntfy.sh/", data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, timeout=20)
    print("ntfy 전송:", r.status_code)


# ───────────────────────── 6. 웹 대시보드 ─────────────────────────

E = html.escape


def badges(r):
    b = ""
    if r.overheated:
        b += "<i>🔥 너무 빨리 오름</i>"
    if earn_soon(r):
        b += f"<i class='ev'>📢 성적표 {dday(r.earn_days)}</i>"
    if r["loss"]:
        b += "<i class='ls'>손해 보는 중</i>"
    return b


def item_html(r, metric):
    return (f"<li><div><b>{E(r['name'])}</b>"
            f"<span class='tk'>{FLAG.get(r.market, '')} {E(short_ticker(r.ticker))} · {E(r.category)}</span></div>"
            f"<span class='m'>{metric}{badges(r)}</span></li>")


def card(title, desc, rows, metric_fn):
    items = "".join(item_html(r, metric_fn(r)) for _, r in rows.iterrows()) \
        or "<li class='empty'>오늘은 여기에 맞는 회사가 없어요</li>"
    return f"<section class='card'><h2>{title}</h2><p>{desc}</p><ol>{items}</ol></section>"


def updown(v, text=None):
    if pd.isna(v):
        return "-"
    cls = "up" if v > 0 else "down" if v < 0 else ""
    return f"<span class='{cls}'>{text or pct(v)}</span>"


def recent_panel(sub, mk):
    p = picks(sub)
    cards = (
        card("🏆 오늘의 추천", "요즘 잘 오르고, 덜 출렁이고, 돈도 잘 버는 회사예요. 100점에 가까울수록 좋아요",
             p["overall"], lambda r: f"{r.total:.0f}점") +
        card("🚀 요즘 쑥쑥 오르는 중", "최근 1~6달 동안 가격이 많이 오른 회사예요",
             p["momentum"], lambda r: f"6달 동안 {updown(r.r6m)}") +
        card("🎢 잠깐 쉬는 중", "원래 잘 오르던 회사인데 요즘 가격이 내려왔어요. 다시 오를 수도, 더 내려갈 수도 있어요 (손해 보는 회사는 뺐어요)",
             p["rebound"], lambda r: f"제일 비쌀 때보다 {updown(r.from_high)}") +
        card("🐢 천천히 꾸준히", "가격이 덜 출렁이고, 6달 동안 조금이라도 오른 회사예요",
             p["stable"], lambda r: f"흔들림 {shake(r.vol60)}") +
        card("💎 싸고 튼튼한 회사", "버는 돈에 비해 가격이 싸고, 돈 버는 실력이 좋은 회사예요",
             p["value"], lambda r: (f"<span>{years_text(r.per)}이면 본전 · 실력 {updown(r.roe)}</span>"
                                    if not pd.isna(r.per) else f"<span>돈 버는 실력 {updown(r.roe)}</span>"))
    )

    cols = [("name", "이름"), ("category", "하는 일"), ("r1d", "1일"), ("r1m", "1달"), ("r3m", "3달"),
            ("r6m", "6달"), ("r12m", "1년"), ("from_high", "제일 비쌀 때보다"), ("vol60", "흔들림"),
            ("per", "본전까지"), ("pbr", "재산의 몇 배"), ("roe", "돈 버는 실력"), ("growth", "작년보다 번 돈"),
            ("earn_days", "성적표 나오는 날"), ("total", "점수")]

    def cell(c, r):
        v = r[c]
        if c == "name":
            marks = (" 🔥" if r.overheated else "") + (" 📢" if earn_soon(r) else "")
            return f"<td>{FLAG.get(r.market, '')} {E(str(v))}{marks}</td>"
        if c == "category":
            return f"<td>{E(str(v))}</td>"
        if c == "vol60":
            return f"<td data-v='{v:.4f}'>{shake(v)}</td>"
        if c == "total":
            return f"<td data-v='{v:.2f}'><b>{v:.0f}</b></td>"
        if c == "per":
            txt = per_text(r)
            dv = v if not pd.isna(v) and v > 0 and not r["loss"] else 9999
            return f"<td data-v='{dv:.2f}'>{txt}</td>"
        if c == "pbr":
            dv = v if not pd.isna(v) and v > 0 else 9999
            return f"<td data-v='{dv:.2f}'>{times(v)}</td>"
        if c == "earn_days":
            if pd.isna(v):
                return "<td data-v='9999'>-</td>"
            return f"<td data-v='{v:.0f}' title='{E(r.earn)}'>{dday(v)}</td>"
        if pd.isna(v):
            return "<td data-v='-999'>-</td>"
        return f"<td data-v='{v:.4f}'>{updown(v)}</td>"

    tid = f"t-{mk}"
    head = "".join(f"<th onclick=\"sortBy('{tid}',{i})\">{n}</th>" for i, (_, n) in enumerate(cols))
    body = "".join("<tr>" + "".join(cell(c, r) for c, _ in cols) + "</tr>"
                   for _, r in sub.sort_values("total", ascending=False).iterrows())

    soon = sub[sub.apply(earn_soon, axis=1)].sort_values("earn_days")
    soon_html = ""
    if not soon.empty:
        chips = "".join(f"<span class='chip'>{FLAG.get(r.market, '')} {E(r['name'])} <b>{dday(r.earn_days)}</b></span>"
                        for _, r in soon.iterrows())
        soon_html = (f"<section class='card soon'><h2>📢 곧 성적표가 나와요</h2>"
                     f"<p>{EARN_SOON}일 안에 회사가 '얼마 벌었는지' 성적표를 내요. 이때는 가격이 크게 오르내릴 수 있어요</p>"
                     f"<div class='chips'>{chips}</div></section>")

    return (f"<div class='grid'>{cards}</div>{soon_html}"
            f"<h3>📋 전부 보기 <span class='hint'>맨 위 제목을 누르면 순서가 바뀌어요</span></h3>"
            f"<div class='tbl'><table id='{tid}'><thead><tr>{head}</tr></thead>"
            f"<tbody>{body}</tbody></table></div>")


def price_box(r):
    """실제 가격으로 얼마 올랐는지 (예: 52,000원 → 188,700원, +136,700원)"""
    p0, p1 = r.get("p0", np.nan), r.get("p1", np.nan)
    if pd.isna(p0) or pd.isna(p1):
        return ""
    m = r["market"]
    d = p1 - p0
    sign = "+" if d > 0 else "-" if d < 0 else ""
    diff = sign + money(abs(d), m)
    return (f"<div class='won pr'><small>실제 가격</small><b>{updown(d, diff)}</b>"
            f"<small>{money(p0, m)} → {money(p1, m)}</small></div>")


def day_text(r):
    """1일: 어느 날 장 마감 기준인지"""
    m = r["market"]
    d = pd.Timestamp(r["end"])
    return f"{MKT_NAME.get(m, '')} {d.month}월 {d.day}일 장 시작부터 마감까지"


def why_html(text):
    return f"<span class='why'>💬 {E(text)}</span>" if text else ""


def long_panel(rows, years):
    ranked = long_rank(rows)
    _, unit, cnt, pname = PERIOD_INFO[years]
    if not rows:
        return (f"<p class='empty big'>여기엔 생긴 지(주식시장에 나온 지) {pname}이 넘은 회사가 없어요.</p>")
    items = []
    for i, r in enumerate(ranked[:LONG_SHOW], 1):
        boxes = "".join(
            (f"<span class='bx n' title='{E(lb)}: 장이 쉬었어요'></span>" if b is None else
             f"<span class='bx {'u' if b > 0 else 'd' if b < 0 else 'n'}' title='{E(lb)}: {pct(b)}'></span>")
            for b, lb in zip(r["blocks"], r["labels"]))
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}")
        ups = r["ups"]
        rest = sum(b is None for b in r["blocks"])
        count_text = day_text(r) + f" · {len(r['blocks'])}{cnt} 중 <b>{ups}{cnt}</b> 올랐어요" if years == "1d" else f"{len(r['blocks'])}{cnt} 중 <b>{ups}{cnt}</b> 올랐어요" + (f" ({rest}{cnt}은 장이 쉬었어요)" if rest else "") + (
            " · 1년에 평균 " + updown(r["cagr"]) if isinstance(years, int) and years > 1 else "")
        n_blocks = len(r["blocks"])
        avg = " · 1년에 평균 " + updown(r["cagr"]) if isinstance(years, int) and years > 1 else ""
        items.append(
            f"<li class='lt{' hide' if i > FIRST_SHOW else ''}'><div class='rk'>{medal}</div><div class='lb'>"
            f"<b>{E(r['name'])}</b>"
            f"<span class='tk'>{FLAG.get(r['market'], '')} {E(short_ticker(r['ticker']))} · {E(r['category'])}</span>"
            f"<div class='boxes'>{boxes}</div>"
            f"<span class='sm'>{count_text}</span>"
            f"{why_html(reason_for(r['ticker'], years, r.get('end')))}</div>"
            f"{price_box(r)}"
            f"<div class='won'><small>100만 원이</small><b>{won(r['total'])}</b>"
            f"<small>{updown(r['total'])}</small></div></li>")
    lost = len(rows) - len(ranked)
    note = f"이 기간 동안 가격이 내려간 회사 {lost}개는 뺐어요. " if lost else ""
    return (f"<p class='how'>🟥 = 그 {unit}에 올랐어요 &nbsp; 🟦 = 그 {unit}에 내렸어요 &nbsp; ⬜ = 장이 쉬었어요 "
            f"(칸을 누르고 있으면 날짜가 보여요)<br>1일 = 그 나라 장 시작부터 마감까지 1시간씩 · 1주 = 7일 · 1달 = 4주 · 1년 = 12달. 마감 시각은 나라마다 달라요 (한국 오후 3:30 · 중국 오후 4시 · 홍콩 오후 5시 · 미국 아침 6시, 한국시간)<br>💬 = 그 기간에 왜 올랐는지 Claude가 뉴스를 보고 짧게 정리한 거예요 (틀릴 수 있어요)<br>{note}주식시장에 나온 지 {pname}이 안 된 회사는 계산에서 빠져요.</p>"
            f"<ol class='longlist'>{''.join(items) or '<li class=empty>여기에 맞는 회사가 없어요</li>'}</ol>"
            + (f"<button class='more' onclick='moreList(this)'>더보기 ({len(ranked[:LONG_SHOW]) - FIRST_SHOW}개 더 · {min(len(ranked), LONG_SHOW)}위까지)</button>"
               if len(ranked[:LONG_SHOW]) > FIRST_SHOW else ""))


CSS = """
@font-face{font-family:'ChosunIlboMyungjo';src:url('https://cdn.jsdelivr.net/gh/projectnoonnu/noonfonts_one@1.0/Chosunilbo_myungjo.woff') format('woff');font-weight:normal;font-display:swap}
:root{color-scheme:dark;--bg:#262624;--fg:#faf9f5;--sub:#a6a39a;--card:#30302e;--line:#3e3e3a;--up:#ff7b6b;--down:#6cb6ff;--acc:#d97757;--soft:#3a3a37}
*{box-sizing:border-box}body{margin:0;font-family:'ChosunIlboMyungjo',Georgia,"Noto Serif KR",serif;-webkit-font-smoothing:antialiased;background:var(--bg);color:var(--fg);-webkit-text-size-adjust:100%}
header{padding:22px 16px 6px;max-width:1100px;margin:auto}h1{font-size:23px;margin:0 0 4px}header p{color:var(--sub);margin:0;font-size:13px}
.guide{max-width:1100px;margin:10px auto 0;padding:0 16px}
.guide details{background:var(--soft);border-radius:14px;padding:12px 14px;font-size:14px;line-height:1.7}
.guide summary{font-weight:700;cursor:pointer}
.bar{position:sticky;top:0;z-index:5;background:var(--bg);max-width:1100px;margin:auto;padding:8px 16px 6px;display:flex;flex-direction:column;gap:6px}
.row{display:flex;gap:5px}
.row.scroll{overflow-x:auto;scrollbar-width:none}.row.scroll::-webkit-scrollbar{display:none}.row.scroll button{flex:none;padding:8px 12px}
.tdesc{font-size:13px;color:var(--sub);margin:2px 2px 10px}
.row button{flex:1 1 0;min-width:0;letter-spacing:-.2px;border:1px solid var(--line);background:transparent;color:var(--sub);border-radius:8px;padding:8px 4px;font-size:13px;font-family:inherit;cursor:pointer;white-space:nowrap}
.row.big button{font-size:15px;font-weight:600;padding:11px 4px;border-radius:10px}
.row button.on{background:var(--soft);border-color:var(--soft);color:var(--fg)}
main{max-width:1100px;margin:auto;padding:8px 16px 40px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px}
.card h2{font-size:17px;margin:0 0 4px}.card p{font-size:13px;color:var(--sub);margin:0 0 10px;line-height:1.5}
.soon{margin-top:12px}.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{background:var(--soft);border-radius:999px;padding:5px 10px;font-size:13px}
ol{margin:0;padding-left:20px}li{padding:8px 0;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:8px;font-size:15px}
li:first-child{border-top:0}li div{display:flex;flex-direction:column}.tk{font-size:12px;color:var(--sub)}
.m{white-space:nowrap;font-weight:700;font-size:14px;display:flex;flex-direction:column;align-items:flex-end;gap:3px}
i{font-style:normal;background:#f59e0b22;color:#d97706;padding:1px 6px;border-radius:6px;font-size:11px;font-weight:600}
i.ev{background:var(--soft);color:var(--acc)}i.ls{background:#6b728022;color:var(--sub)}
.up{color:var(--up)}.down{color:var(--down)}
.empty{color:var(--sub)}.big{font-size:15px;line-height:1.7;text-align:center;padding:30px 0}
h3{margin:26px 0 8px;font-size:16px}.hint{font-weight:400;font-size:12px;color:var(--sub)}
.tbl{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:16px}
table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:9px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th{cursor:pointer;color:var(--sub);font-weight:600;position:sticky;top:0;background:var(--card)}
th:nth-child(-n+2),td:nth-child(-n+2){text-align:left}
.how{font-size:13px;color:var(--sub);line-height:1.6;margin:4px 0 10px}
.longlist .lt.hide{display:none}.longlist.open .lt.hide{display:flex}
.more{display:block;width:100%;margin:8px 0 0;padding:11px;border:1px dashed var(--line);border-radius:12px;background:transparent;color:var(--sub);font:inherit;font-size:14px;cursor:pointer}
.longlist{list-style:none;padding:0;background:var(--card);border:1px solid var(--line);border-radius:16px}
.lt{padding:12px 14px;align-items:center}.rk{width:30px;flex:0 0 30px;font-size:18px;font-weight:700;text-align:center}
.lb{flex:1;min-width:0;gap:2px}.lb .boxes{display:flex;flex-direction:row;flex-wrap:wrap;gap:3px;margin:5px 0 3px}
.bx{width:14px;height:14px;border-radius:3px;display:inline-block}.bx.u{background:var(--up)}.bx.d{background:var(--down)}.bx.n{background:var(--line)}
.sm{font-size:13px;color:var(--sub)}.sm b{color:var(--fg)}
.why{display:block;font-size:13px;line-height:1.5;color:var(--fg);background:var(--soft);border-radius:8px;padding:6px 9px;margin-top:6px}
.won{text-align:right;flex:0 0 auto;display:flex;flex-direction:column}.won b{font-size:17px}.won small{font-size:11px;color:var(--sub)}
.won.pr{border-right:1px solid var(--line);padding-right:10px;margin-right:2px}.won.pr b{font-size:15px}.won.pr small:last-child{white-space:nowrap}
@media (max-width:480px){.lt{flex-wrap:wrap}.lt .lb{flex:1 1 calc(100% - 40px)}.lt .won{flex:1 1 40%;margin-top:6px;text-align:left;padding-left:38px}.lt .won.pr{border-right:0}}
.view,.mk,.yr{display:none}.view.on,.mk.on,.yr.on{display:block}
.demo{background:#f59e0b;color:#111;padding:8px 16px;text-align:center;font-weight:600}
footer{color:var(--sub);font-size:12px;line-height:1.7;margin-top:26px}
"""

JS = """
function moreList(b){const l=b.previousElementSibling;if(l)l.classList.add('open');b.remove();}
(function(){function chk(){fetch('version.json?t='+Date.now(),{cache:'no-store'}).then(r=>r.ok?r.json():null).then(v=>{
 if(v&&v.build&&v.build!==BUILD){const u=new URL(location.href);if(u.searchParams.get('v')===v.build)return;
 u.searchParams.set('v',v.build);location.replace(u.toString());}}).catch(()=>{});}
chk();document.addEventListener('visibilitychange',()=>{if(!document.hidden)chk();});window.addEventListener('pageshow',e=>{if(e.persisted)chk();});})();
let dir={};function sortBy(id,i){const tb=document.querySelector('#'+id+' tbody');const rows=[...tb.rows];const k=id+i;dir[k]=!dir[k];
rows.sort((a,b)=>{const x=a.cells[i],y=b.cells[i];const vx=x.dataset.v??x.textContent,vy=y.dataset.v??y.textContent;
const nx=parseFloat(vx),ny=parseFloat(vy);const c=isNaN(nx)?String(vx).localeCompare(vy,'ko'):nx-ny;return dir[k]?-c:c;});
rows.forEach(r=>tb.appendChild(r));}
const st={v:'recent',m:'all',y:'5'};
function set(k,val){st[k]=val;draw();}
function draw(){
 document.querySelectorAll('[data-set]').forEach(b=>{const [k,v]=b.dataset.set.split(':');b.classList.toggle('on',st[k]===v);});
 document.querySelectorAll('.view').forEach(e=>e.classList.toggle('on',e.dataset.v===st.v));
 document.querySelectorAll('.mk').forEach(e=>e.classList.toggle('on',e.dataset.m===st.m));
 document.querySelectorAll('.yr').forEach(e=>e.classList.toggle('on',e.dataset.y===st.y));
 document.getElementById('yrow').style.display=st.v==='long'?'flex':'none';
 document.querySelectorAll('.row.scroll').forEach(r=>{const b=r.querySelector('button.on');if(b)r.scrollLeft=b.offsetLeft-r.offsetLeft-(r.clientWidth-b.offsetWidth)/2;});
 history.replaceState(null,'','#'+st.v+'-'+st.m+'-'+st.y);}
(function(){const h=location.hash.slice(1).split('-');if(h[0]==='recent'||h[0]==='long')st.v=h[0];
if(h[1]&&document.querySelector('[data-set="m:'+h[1]+'"]'))st.m=h[1];if(h[2])st.y=h[2];draw();})();
"""


def dashboard(df, long, today, demo, build=""):
    banner = "<div class='demo'>⚠ 가짜(데모) 데이터로 만든 미리보기예요</div>" if demo else ""
    markets = [("all", "전체", None, "모든 테마의 회사를 한꺼번에 비교해요")] + [
        (k, n, k, d) for k, n, d in THEMES if not in_theme(df, k).empty]

    recent = "".join(
        f"<div class='mk' data-m='{k}'><p class='tdesc'>{E(d)} · {len(in_theme(df, c))}개</p>"
        f"{recent_panel(in_theme(df, c), k)}</div>"
        for k, _, c, d in markets)
    long_html = ""
    for k, _, c, _ in markets:
        inner = "".join(
            f"<div class='yr' data-y='{y}'>"
            f"{long_panel([r for r in long[y] if c is None or c in str(r.get('theme', '')).split('|')], y)}</div>"
            for y in PERIODS)
        long_html += f"<div class='mk' data-m='{k}'>{inner}</div>"

    vbtn = ("<button data-set='v:recent' onclick=\"set('v','recent')\">📅 요즘 추천</button>"
            "<button data-set='v:long' onclick=\"set('v','long')\">🌱 오래 꾸준히 오른</button>")
    mbtn = "".join(f"<button data-set='m:{k}' onclick=\"set('m','{k}')\">{lb}</button>"
                   for k, lb, _, _ in markets)
    ybtn = "".join(f"<button data-set='y:{y}' onclick=\"set('y','{y}')\">{PERIOD_INFO[y][0]}</button>"
                   for y in PERIODS)
    counts = " · ".join(f"{f} {int((df.market == c).sum())}개" for c, f, _ in MARKETS
                        if (df.market == c).any())

    guide = """
<div class="guide"><details><summary>👀 이 페이지 보는 법 (눌러서 열기)</summary>
<b>주식</b>은 회사를 아주 잘게 나눈 조각이에요. 조각을 사면 그 회사의 주인이 조금 되는 거예요.<br>
회사가 장사를 잘하면 조각 가격이 오르고, 못하면 내려가요. 여기서는 한국·미국 회사 146개를 매일 살펴봐요.<br>
회사들을 <b>테마</b>(2차전지, AI 전력, 로봇처럼 비슷한 일을 하는 회사 묶음)로 나눠 보여줘요. 위쪽 테마 버튼을 옆으로 밀면 더 있어요.<br>
점수는 <b>같은 나라 회사끼리</b> 비교해서 매겨요. 100점에 가까울수록 좋아요.<br><br>
<b>📅 요즘 추천</b><br>
&nbsp;&nbsp;🏆 오늘의 추천: 잘 오르고 + 덜 출렁이고 + 돈도 잘 버는 회사<br>
&nbsp;&nbsp;🚀 쑥쑥 오르는 중: 요즘 가격이 많이 오른 회사<br>
&nbsp;&nbsp;🎢 잠깐 쉬는 중: 잘 오르다가 요즘 잠깐 내려온 회사<br>
&nbsp;&nbsp;🐢 천천히 꾸준히: 가격이 덜 출렁이는 회사<br>
&nbsp;&nbsp;💎 싸고 튼튼한 회사: 버는 돈에 비해 싸고, 돈 버는 실력이 좋은 회사<br><br>
<b>표에 나오는 말</b><br>
&nbsp;&nbsp;<b>본전까지</b>: 회사가 1년에 버는 돈으로 지금 가격을 채우려면 몇 년 걸리는지. <b>짧을수록 싸요</b><br>
&nbsp;&nbsp;<b>재산의 몇 배</b>: 회사가 가진 재산보다 가격이 몇 배 비싼지. <b>작을수록 싸요</b><br>
&nbsp;&nbsp;<b>돈 버는 실력</b>: 회사가 가진 돈 100원으로 1년에 몇 원을 벌었는지. <b>클수록 장사를 잘해요</b><br>
&nbsp;&nbsp;<b>작년보다 번 돈</b>: 작년 이맘때보다 번 돈이 얼마나 늘었는지(+) 줄었는지(−)<br>
&nbsp;&nbsp;<b>성적표 나오는 날</b>: 회사가 '얼마 벌었는지' 발표하는 날. D-3은 3일 남았다는 뜻이에요<br>
&nbsp;&nbsp;<b>흔들림</b>: 가격이 하루하루 얼마나 크게 오르내리는지 (작음 · 보통 · 큼)<br>
&nbsp;&nbsp;<b>제일 비쌀 때보다</b>: 지난 1년 중 가장 비쌌던 날보다 지금 얼마나 싸졌는지<br>
&nbsp;&nbsp;<span class="up">빨간 숫자</span>는 올랐다는 뜻, <span class="down">파란 숫자</span>는 내렸다는 뜻이에요<br><br>
<b>🌱 오래 꾸준히 오른</b>: 1주~20년 동안 기간마다 올랐는지 색깔 칸으로 보여줘요.<br>
<b>🔥 너무 빨리 오름</b>: 짧은 시간에 많이 올라서, 잠깐 내려갈 수도 있으니 조심<br>
<b>손해 보는 중</b>: 요즘 회사가 버는 돈보다 쓰는 돈이 더 많아요<br><br>
⚠ 회사 하나는 여러 회사를 묶은 ETF보다 가격이 훨씬 크게 오르내려요. 한 회사에만 몰아서 사지 않는 게 좋아요.
</details></div>"""

    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate"><meta http-equiv="Pragma" content="no-cache"><meta http-equiv="Expires" content="0">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="theme-color" content="#262624">
<title>개별종목 추천 대시보드</title><style>{CSS}</style></head><body>{banner}
<header><h1>📈 오늘의 개별종목 추천</h1><p>{today} 업데이트 · {counts} 회사를 테마별로 살펴봤어요</p></header>
{guide}
<nav class="bar"><div class="row big">{vbtn}</div><div class="row scroll">{mbtn}</div>
<div class="row" id="yrow">{ybtn}</div></nav>
<main>
<div class="view" data-v="recent">{recent}</div>
<div class="view" data-v="long">{long_html}</div>
<footer>
💡 회사가 나눠주는 돈(배당금)도 더해서 계산했고, 살 때 내는 수수료와 세금은 빼지 않았어요.
회사 성적(본전까지·재산의 몇 배·돈 버는 실력)과 성적표 나오는 날은 야후 파이낸스와 네이버 증권 자료라 가끔 비어 있을 수 있어요.<br>
'100만 원이 → 얼마'는 그 기간 처음에 100만 원어치 샀다면 지금 얼마가 됐을지 보여줘요.<br>
⚠ 지나간 숫자로 계산한 참고용이에요. 앞으로도 똑같이 오른다는 약속도, 사라는 뜻도 아니에요.
</footer></main>
<script>const BUILD="{build}";{JS}</script></body></html>"""


# ───────────────────────── 7. 실행 ─────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="가짜 데이터로 테스트")
    args = ap.parse_args()

    universe = load_universe()
    tickers = universe.ticker.tolist()
    today_d = datetime.now(KST).date()
    if args.demo:
        closes, funds = demo_prices(tickers), demo_fundamentals(tickers, today_d)
    else:
        closes, funds = fetch_prices(tickers), fetch_fundamentals(tickers, today_d)

    (demo_intraday(closes) if args.demo else fetch_intraday(tickers))
    df = build_table(universe, closes, funds, today_d)
    df = apply_intraday(df)
    long = build_long(universe, closes)
    today = today_d.strftime("%Y-%m-%d")

    build = datetime.now(KST).strftime("%Y%m%d%H%M%S")
    os.makedirs(DOCS, exist_ok=True)
    with open(os.path.join(DOCS, "index.html"), "w", encoding="utf-8") as f:
        f.write(dashboard(df, long, today, args.demo, build))
    with open(os.path.join(DOCS, "version.json"), "w", encoding="utf-8") as f:
        json.dump({"build": build}, f)
    df.to_csv(os.path.join(DOCS, "latest.csv"), index=False, encoding="utf-8-sig")

    close = os.environ.get("CLOSE_MARKET", "").upper()   # 장 마감 때 실행하면 KR/CN/HK
    msg = close_message(df, close, today) if close else phone_message(df, long, today)
    print(msg)

    url = os.environ.get("DASHBOARD_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not url and repo:
        owner, name = repo.split("/")
        url = f"https://{owner}.github.io/{name}/"
    if url:   # 알림에서 열 때 예전에 저장된 화면 대신 항상 새 화면이 나오게
        url = f"{url}{'&' if '?' in url else '?'}v={build}"
    if os.environ.get("GITHUB_EVENT_NAME") == "push":
        print("코드·이유 파일을 고쳐서 다시 만든 거라 알림은 보내지 않아요.")
    elif not args.demo:
        if close:
            if msg:
                send_ntfy(f"{FLAG.get(close, '')} {MKT_NAME.get(close, close)}장 마감 · 오늘 많이 오른 종목", msg,
                          f"{url}#long-all-1d" if url else url)
        else:
            send_ntfy("오늘의 개별종목 추천", msg, url)


if __name__ == "__main__":
    sys.exit(main())
