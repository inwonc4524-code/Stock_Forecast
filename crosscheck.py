"""
야후 파이낸스 가격을 다른 사이트와 비교해서 틀린 걸 고치거나 빼요.

  🇰🇷 한국(주식·ETF): 네이버 증권 일별 시세
  🇺🇸 미국 · 🇯🇵 일본 · 🇭🇰 홍콩 · 🇨🇳 중국 · 🇻🇳 베트남: 네이버 증권 해외주식 일별 시세
  (영국·독일·인도네시아·싱가포르·캐나다는 무료로 비교할 사이트가 마땅치 않아 야후 자료 안에서만 점검)

하는 일
  1. 마지막 마감 가격이 2% 넘게 다르면 → 다른 사이트 가격으로 고쳐요
  2. 1일·1주·1달·1년 수익률이 크게 다르면 → 그 기간 순위에서 이번엔 빼요
결과는 docs/crosscheck.json 에 남기고, 대시보드 맨 아래에 한 줄로 보여줘요.
"""

import ast
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

import pricecache

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
WORLD = {"US", "JP", "HK", "CN", "VN"}   # 네이버 해외주식으로 비교할 수 있는 나라
FAILS = {}   # 사이트별 연속 실패 수 (계속 실패하면 이번 실행에선 그만 물어봄)
ROTATE = 150                 # 해외는 한 번에 이만큼씩 돌아가며 비교 (+ 크게 움직인 곳은 항상)
BAD = {}                     # 티커 → 순위에서 뺄 기간 {"1d","1w","1m",1}
OPEN = {}                    # 티커 → (날짜, 거래소 공식 시작 가격) — 1일 '장 시작→마감' 계산용
REPLACED = {}                # 티커 → (실제 가격, 수정종가) 네이버 값으로 맞춘 가격 (장 마감 실행에서 다시 쓰려고 저장)
VN_DAYS = 30                 # 베트남은 최근 이만큼(날)을 네이버 값으로 맞춰요 (2026-11-21 사용자 요청)
REPORT = {"source_ok": 0, "source_none": 0, "fixed_last": [], "excluded": [], "no_source_markets": []}


# ───────── 다른 사이트에서 받아오기 ─────────

def naver_series(code):
    end = pd.Timestamp.today().strftime("%Y%m%d")
    start = (pd.Timestamp.today() - pd.Timedelta(days=400)).strftime("%Y%m%d")
    url = (f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1"
           f"&startTime={start}&endTime={end}&timeframe=day")
    txt = requests.get(url, headers=UA, timeout=15).text.strip()
    rows = ast.literal_eval(txt.replace("\n", ""))
    data = [(pd.Timestamp(str(r[0])), float(r[4])) for r in rows[1:] if r and r[4]]
    last = [r for r in rows[1:] if r and r[4]]
    if last and last[-1][1]:
        OPEN[code] = (pd.Timestamp(str(last[-1][0])), float(last[-1][1]))
    return pd.Series(dict(data)).sort_index() if data else None


def naver_world_codes(ticker, mk):
    """야후 티커 → 네이버 해외주식 코드 후보"""
    if mk == "US":
        base = ticker
        if "-" in base:   # BRK-B → BRKb
            a, c = base.split("-", 1)
            base = a + c.lower()
        return [base + ".O", base, base + ".K"]   # 나스닥 · 뉴욕 · 기타
    if mk == "VN":
        return [ticker.replace(".VN", ".HM"), ticker.replace(".VN", ".HN")]
    return [ticker]   # 일본 .T · 홍콩 .HK · 중국 .SS/.SZ 는 그대로


def naver_world_series(ticker, mk):
    end = pd.Timestamp.today()
    start = end - pd.Timedelta(days=400)
    for code in naver_world_codes(ticker, mk):
        url = (f"https://api.stock.naver.com/chart/foreign/item/{code}/day?"
               f"startDateTime={start:%Y%m%d}0000&endDateTime={end:%Y%m%d}2359")
        r = requests.get(url, headers=UA, timeout=12)
        if not r.ok:
            continue
        data = r.json()
        if isinstance(data, list) and data:
            s = pd.Series({pd.Timestamp(str(x["localDate"])): float(x["closePrice"]) for x in data
                           if x.get("closePrice")}).sort_index()
            if len(s):
                lastx = max((x for x in data if x.get("closePrice")), key=lambda x: str(x["localDate"]))
                if lastx.get("openPrice"):
                    OPEN[ticker] = (pd.Timestamp(str(lastx["localDate"])), float(lastx["openPrice"]))
                return s
    return None


def source(ticker, mk):
    kind = "kr" if mk == "KR" else "world" if mk in WORLD else None
    if kind is None or FAILS.get(kind, 0) >= 15:   # 15번 연속 실패하면 그 사이트는 이번엔 쉬기
        return None
    try:
        s = naver_series(ticker.split(".")[0]) if kind == "kr" else naver_world_series(ticker, mk)
        if kind == "kr" and ticker.split(".")[0] in OPEN:
            OPEN[ticker] = OPEN.pop(ticker.split(".")[0])
    except Exception:
        s = None
    FAILS[kind] = 0 if s is not None else FAILS.get(kind, 0) + 1
    return s


# ───────── 비교 ─────────

def _ret(s, end, days):
    if days == "1d":
        if len(s) < 2:
            return np.nan
        return s.iloc[-1] / s.iloc[-2] - 1
    start = end - pd.Timedelta(days=days)
    if s.index[0] > start:
        return np.nan
    return s.iloc[-1] / s.asof(start) - 1


PERIODS = [("1d", "1d", 0.05), ("1w", 7, 0.08), ("1m", 28, 0.12), (1, 365, 0.25)]   # (기간, 날짜, 허용 차이)



def align_split(src, y):
    """네이버(비교용) 가격 중 야후와 분할 비율(2·4·5·8·10배 …)만큼 차이 나는 날은 야후 기준으로 나눠 맞춰요.
    2026-11-21 점검: 샌디스크는 11/20 에 1→8 분할하면서 하루 −16% 떨어져서, 네이버 쪽 하루 튐이 10.5배로 보여
    fix_jumps 가 8배가 아니라 10배로 잘못 맞췄어요. 같은 날 야후 값과 하루하루 비교하면 8배를 바로 찾을 수 있어요."""
    try:
        both = src.index.intersection(y.index)
        if not len(both):
            return src
        r = src.loc[both] / y.loc[both]
        src = src.copy()
        n = 0
        for d, v in r.items():
            if pd.notna(v) and v > 0 and abs(v - 1) > 0.3:
                f = pricecache._nice(float(v))
                if f not in (None, 1):
                    src.loc[d] = src.loc[d] / f
                    n += 1
        return src
    except Exception:
        return src


def vn_from_naver(base, src, last):
    """베트남 종목의 최근 VN_DAYS 일 가격을 네이버 값으로 바꿔요 (2026-11-21 사용자 요청).
    야후 베트남 가격은 마지막 날·그 전날이 하루 전 값인 때가 많아요(테콤은행 11/20: 야후 20,600 · 실제 21,200).
    베트남은 애프터마켓이 없어서 네이버 값 = 거래소 공식 종가.
    야후의 옛날 가격(배당 조정 등)과 높이를 맞추려고, 날마다 '야후 ÷ 네이버' 비율의 7일 가운데 값을 곱해요
    (하루 밀린 값 때문에 생기는 흔들림은 없애고, 배당으로 생긴 높이 차이는 살려요)."""
    if base is None or not len(base):
        return None
    win0 = last - pd.Timedelta(days=VN_DAYS)
    s = src[(src.index > win0) & (src.index <= last)].dropna()
    s = s[s > 0]
    both = s.index.intersection(base.index)
    if len(s) < 5 or len(both) < 5:
        return None
    r = (base.loc[both] / s.loc[both]).reindex(s.index)
    k = r.rolling(7, center=True, min_periods=3).median().interpolate(limit_direction="both")
    if k.isna().any():
        return None
    new = s * k
    return pd.concat([base[base.index <= win0], new]).sort_index()

def run(closes, raw, mkt_of, names=None, state_path=None):
    """closes: 수익률용(배당 포함) / raw: 실제 가격. 틀린 마지막 가격은 고치고, 수익률이 크게 다른 기간은 BAD에 기록."""
    names = names or {}
    tickers = [t for t in closes if t in raw and len(raw[t]) > 2]
    REPORT["no_source_markets"] = sorted({mkt_of.get(t, "") for t in tickers}
                                         - WORLD - {"KR", ""})

    # 비교할 곳 고르기: 한국은 전부, 해외는 크게 움직인 곳 + 돌아가며 ROTATE개
    def big_move(t):
        s = raw[t]
        r1 = _ret(s, s.index[-1], "1d")
        r7 = _ret(s, s.index[-1], 7)
        r28 = _ret(s, s.index[-1], 28)
        r365 = _ret(s, s.index[-1], 365)
        return any(abs(x) > th for x, th in ((r1, .08), (r7, .2), (r28, .35), (r365, 1.5)) if not pd.isna(x))

    rot = 0
    if state_path:
        try:
            rot = json.load(open(state_path)).get("rot", 0)
        except Exception:
            rot = 0
    foreign = [t for t in tickers if mkt_of.get(t) in WORLD]
    pick = [t for t in tickers if mkt_of.get(t) in ("KR", "US")]   # 한국·미국은 매번 전부 (알림에 바로 쓰여서)
    pick += [t for t in foreign if big_move(t)]
    # 베트남은 야후 마지막 날 가격이 하루 전 값인 때가 많아서(2026-11-17~20 매일 8~10곳 2% 넘게 틀림) 매번 전부 비교 (2026-11-21 점검)
    pick += [t for t in foreign if mkt_of.get(t) == "VN"]
    if foreign:
        start = rot % len(foreign)
        pick += (foreign[start:] + foreign[:start])[:ROTATE]
    pick = list(dict.fromkeys(pick))

    def one(t):
        time.sleep(0.15)
        return t, source(t, mkt_of.get(t))

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:   # 동시에 4곳까지만 (네이버가 막지 않게)
        got = dict(ex.map(one, pick))
    took = time.time() - t0

    for t, src in got.items():
        if src is None or len(src) < 3:
            REPORT["source_none"] += 1
            continue
        REPORT["source_ok"] += 1
        # 네이버 해외 가격은 분할 전 옛날 가격을 그대로 둘 때가 있어서, 비교 전에 야후 쪽과 같은 방법으로 분할 튐을 맞춰요
        # (ETF 봇 2026-11-14 와 같은 방법, 개별종목 봇은 2026-11-21 점검 때 추가)
        if mkt_of.get(t) != "KR":   # 한국은 네이버가 기준이라 그대로(망가진 야후 가격을 찾는 데 써요)
            src = align_split(src, raw[t])   # 먼저 같은 날 야후와 분할 비율 맞추기 (2026-11-21)
        try:
            src = pricecache.fix_jumps(src, 3.0, f"(비교용 네이버) {t}")
        except Exception:
            pass
        y = raw[t]
        last = y.index[-1]
        if last not in src.index:
            continue   # 다른 사이트가 아직 그 날 가격이 없음 → 비교 안 함
        if mkt_of.get(t) == "VN":
            ny = vn_from_naver(y, src, last)
            nc = vn_from_naver(closes.get(t), src, last) if t in closes else None
            if ny is not None:
                diff = (ny / y.reindex(ny.index) - 1).abs().iloc[-VN_DAYS:].max()
                raw[t] = ny
                if nc is not None:
                    closes[t] = nc
                REPLACED[t] = (raw[t].copy(), closes.get(t).copy() if t in closes else None)
                if pd.notna(diff) and diff > 0.005:
                    REPORT.setdefault("vn_naver", []).append(names.get(t, t))
                y = raw[t]
                last = y.index[-1]
        # 1) 마지막 가격: 2% 넘게 다를 때만 고침
        #    (주의: 네이버 한국 일별 가격은 장 마감 뒤 넥스트레이드 애프터마켓(~20시)까지 합친 '통합' 값이라
        #     거래소 정규장 종가(15:30)와 몇백~몇천 원 다를 수 있어요. 그래서 작은 차이는 고치지 않아요 — 2026-11-13 확인)
        sv, yv = float(src.loc[last]), float(y.iloc[-1])
        # 2026-11-21 점검: 액면분할 날 네이버가 아직 분할 전 가격을 주면(샌디스크 8→1 분할: 야후 94.39 · 네이버 755.16,
        # 키엔스 1→5 분할: 야후 14,722 · 네이버 73,610) 맞는 야후 값을 틀린 값으로 '고쳐' 버렸어요.
        # 두 값 차이가 분할 비율(2·3·4·5·8·10배 …)과 맞으면 분할 차이로 보고 이번엔 비교하지 않아요.
        if sv > 0 and yv > 0 and abs(yv / sv - 1) > 0.3 and pricecache._nice(yv / sv) not in (None, 1):
            REPORT.setdefault("split_skip", []).append(f"{names.get(t, t)} (야후 {yv:,.2f} · 네이버 {sv:,.2f})")
            continue
        # 한국은 정규장 종가(15:30)를 쓰기로 해서(2026-11-13 사용자 결정), 통합 가격인 네이버 값으로 바꾸지 않아요
        if mkt_of.get(t) != "KR" and sv > 0 and abs(yv / sv - 1) > 0.02:
            ratio = sv / yv
            raw[t] = pd.concat([y.iloc[:-1], pd.Series([sv], index=[last])])
            c = closes[t]
            if c.index[-1] == last:
                closes[t] = pd.concat([c.iloc[:-1], pd.Series([float(c.iloc[-1]) * ratio], index=[last])])
            REPORT["fixed_last"].append(f"{names.get(t, t)} ({yv:,.2f}→{sv:,.2f})")
            y = raw[t]
        # 2) 기간 수익률
        s2 = src[src.index <= last]
        bad = set()
        for key, days, tol in PERIODS:
            a, b = _ret(y, last, days), _ret(s2, last, days)
            if pd.isna(a) or pd.isna(b):
                continue
            if abs(a - b) > tol and abs(a - b) > 0.25 * max(abs(a), abs(b)):
                bad.add(key)
        if bad:
            BAD[t] = bad
            per = ",".join({"1d": "1일", "1w": "1주", "1m": "1달", 1: "1년"}[k] for k in
                           [k for k, _, _ in PERIODS if k in bad])
            REPORT["excluded"].append(f"{names.get(t, t)} ({per})")

    if state_path:
        try:
            json.dump({"rot": rot + ROTATE}, open(state_path, "w"))
        except Exception:
            pass
    if REPORT.get("vn_naver"):
        print(f"베트남 최근 {VN_DAYS}일을 네이버(거래소 공식 종가) 값으로 맞춘 곳 {len(REPORT['vn_naver'])}:",
              ", ".join(REPORT["vn_naver"][:15]))
    if REPORT.get("split_skip"):
        print("액면분할 차이로 이번엔 비교하지 않은 곳:", ", ".join(REPORT["split_skip"][:15]))
    print(f"다른 사이트와 비교: {REPORT['source_ok']}곳 비교 · 마지막 가격 고침 {len(REPORT['fixed_last'])}곳 · "
          f"순위에서 뺀 곳 {len(REPORT['excluded'])}곳 · 비교 자료 없음 {REPORT['source_none']}곳 · {took:.0f}초")
    return BAD


def save(path):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(REPORT, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("비교 기록 저장 실패:", e)


def note_html(esc=lambda x: x):
    if not REPORT["source_ok"]:
        return ""
    t = (f"🔎 야후 가격을 다른 사이트(네이버 증권: 한국·미국·일본·홍콩·중국·베트남)와 비교했어요: "
         f"{REPORT['source_ok']}곳 확인")
    if REPORT["fixed_last"]:
        t += f" · 마지막 가격을 고친 곳 {len(REPORT['fixed_last'])}곳"
    if REPORT["excluded"]:
        t += " · 숫자가 서로 달라 이번엔 순위에서 뺀 곳: " + ", ".join(esc(x) for x in REPORT["excluded"][:10])
        if len(REPORT["excluded"]) > 10:
            t += f" 외 {len(REPORT['excluded']) - 10}곳"
    if REPORT["no_source_markets"]:
        t += f" · 비교할 사이트가 없는 나라({', '.join(REPORT['no_source_markets'])})는 야후 자료 안에서만 점검했어요"
    return t + "<br>"
