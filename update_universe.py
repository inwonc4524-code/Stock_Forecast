"""
종목 목록(stocks.csv) 자동 업데이트 — 한 달에 한 번 GitHub Actions가 실행해요.

기준
  🇰🇷 한국: 코스피+코스닥 시가총액 상위 100개 (우선주·스팩 제외)  ← 네이버 증권
  🇺🇸 미국: S&P500 회사 중 시가총액 상위 150개                    ← 위키백과 + 야후 파이낸스
  🌏 홍콩·중국·일본·베트남·인도네시아·독일·영국·싱가포르·캐나다:
     intl_candidates.py 후보 중 시가총액 상위 50개씩                ← 야후 파이낸스
지금 목록에 있는 회사(테마·업종으로 넣은 회사 포함)는 그대로 두고, 순위 안에 새로 들어온 회사만 더해요.
순위에서 밀려난 회사도 지우지 않아요.

실행: python update_universe.py
"""

import csv
import io
import os
import re
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(HERE, "stocks.csv")
KR_TOP = 100
US_TOP = 150
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

# 업종(GICS) → 테마
US_THEME_BY_SUB = [
    ("Semiconductor", "semi"), ("Aerospace & Defense", "defense"), ("Electric Utilities", "power"),
    ("Independent Power", "power"), ("Multi-Utilities", "power"), ("Electrical Components", "power"),
    ("Biotechnology", "bio"), ("Pharmaceuticals", "bio"), ("Health Care Equipment", "bio"),
    ("Life Sciences", "bio"), ("Interactive Media", "net"), ("Internet", "net"),
    ("Entertainment", "net"), ("Application Software", "net"), ("Systems Software", "net"),
    ("Industrial Machinery", "robot"),
]
US_THEME_BY_SECTOR = {"Financials": "fin", "Health Care": "bio", "Utilities": "power"}
US_SECTOR_KO = {
    "Information Technology": "기술", "Health Care": "헬스케어", "Financials": "금융",
    "Consumer Discretionary": "소비(쇼핑·자동차)", "Consumer Staples": "필수소비(음식·생활)",
    "Communication Services": "통신·미디어", "Industrials": "산업재", "Energy": "에너지",
    "Utilities": "전기·가스", "Real Estate": "부동산", "Materials": "소재",
}
KR_THEME_WORDS = [
    (("반도체", "하이닉스", "테크닉스", "솔브레인", "하나마이크론"), "semi"),
    (("바이오", "제약", "셀트리온", "약품", "메디", "헬스"), "bio"),
    (("금융", "은행", "증권", "보험", "생명", "화재", "카드", "캐피탈"), "fin"),
    (("에코프로", "엘앤에프", "에너지솔루션", "2차전지", "배터리", "퓨처엠"), "bat"),
    (("전선", "일렉트릭", "ELECTRIC", "중공업", "에너빌리티", "전력"), "power"),
    (("에어로스페이스", "넥스원", "항공우주", "로템", "시스템", "조선", "오션"), "defense"),
    (("로봇", "로보"), "robot"),
    (("게임", "엔터", "엔씨", "넷마블", "카카오", "NAVER", "하이브", "크래프톤", "펄어비스", "JYP", "에스엠"), "net"),
]


def num(s):
    try:
        return float(re.sub(r"[^\d.\-]", "", str(s)) or "nan")
    except ValueError:
        return float("nan")


def load():
    with open(CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r.setdefault("theme", "")
    return rows


def save(rows):
    with open(CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ticker", "name", "market", "category", "theme"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})


# ───────── 한국: 네이버 증권 시가총액 순위 ─────────

def kr_top():
    out = []
    for mkt, suffix in (("KOSPI", ".KS"), ("KOSDAQ", ".KQ")):
        for page in (1, 2):
            url = f"https://m.stock.naver.com/api/stocks/marketValue/{mkt}?page={page}&pageSize=100"
            try:
                data = requests.get(url, headers=UA, timeout=20).json()
            except Exception as e:
                print(f"네이버 {mkt} {page}쪽 못 받음: {e}")
                continue
            items = data.get("stocks") if isinstance(data, dict) else data
            for it in items or []:
                code = str(it.get("itemCode") or it.get("code") or "")
                name = str(it.get("stockName") or it.get("itemName") or it.get("name") or "")
                cap = num(it.get("marketValue") or it.get("marketSum") or it.get("marketCap"))
                end_type = str(it.get("stockEndType") or "stock").lower()
                if not re.fullmatch(r"\d{6}", code) or end_type not in ("stock", ""):
                    continue
                if re.search(r"(우|우B|우C|\(전환\))$", name) or "스팩" in name:
                    continue
                out.append({"ticker": code + suffix, "name": name, "cap": cap,
                            "category": "코스닥" if suffix == ".KQ" else "코스피"})
    out = [x for x in out if x["cap"] == x["cap"]]   # NaN 빼기
    out.sort(key=lambda x: x["cap"], reverse=True)
    print(f"한국 시가총액 순위 받은 회사: {len(out)}개")
    return out[:KR_TOP]


def kr_theme(name):
    for words, t in KR_THEME_WORDS:
        if any(w in name for w in words):
            return t
    return ""


# ───────── 미국: S&P500 시가총액 순위 ─────────

def us_top():
    import pandas as pd
    import yfinance as yf

    html = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers=UA, timeout=30).text
    table = pd.read_html(io.StringIO(html))[0]
    table["ticker"] = table["Symbol"].astype(str).str.replace(".", "-", regex=False)
    print(f"S&P500 회사 목록: {len(table)}개")

    def cap(t):
        try:
            return float(yf.Ticker(t).fast_info["market_cap"] or 0)
        except Exception:
            return 0.0

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as ex:
        caps = list(ex.map(cap, table.ticker))
    table["cap"] = caps
    table = table[table.cap > 0].sort_values("cap", ascending=False).head(US_TOP)
    out = []
    for _, r in table.iterrows():
        sub = str(r.get("GICS Sub-Industry", ""))
        sec = str(r.get("GICS Sector", ""))
        theme = next((t for key, t in US_THEME_BY_SUB if key in sub), US_THEME_BY_SECTOR.get(sec, ""))
        out.append({"ticker": r.ticker, "name": str(r.get("Security", r.ticker)),
                    "category": US_SECTOR_KO.get(sec, sec), "theme": theme})
    return out


INTL_TOP = 50
INDUSTRY_THEME = [   # 야후 업종(industry) → 테마
    ("Semiconductor", "semi"), ("Biotechnology", "bio"), ("Drug Manufacturers", "bio"),
    ("Medical", "bio"), ("Diagnostics", "bio"), ("Banks", "fin"), ("Insurance", "fin"),
    ("Capital Markets", "fin"), ("Financial", "fin"), ("Credit Services", "fin"),
    ("Aerospace & Defense", "defense"), ("Utilities", "power"), ("Electrical Equipment", "power"),
    ("Internet", "net"), ("Electronic Gaming", "net"), ("Entertainment", "net"),
    ("Software", "net"), ("Specialty Industrial Machinery", "robot"),
]
SECTOR_KO = {
    "Technology": "기술", "Healthcare": "헬스케어", "Financial Services": "금융",
    "Consumer Cyclical": "소비(쇼핑·자동차)", "Consumer Defensive": "필수소비(음식·생활)",
    "Communication Services": "통신·미디어", "Industrials": "산업재", "Energy": "에너지",
    "Utilities": "전기·가스", "Real Estate": "부동산", "Basic Materials": "소재",
}


def intl_top():
    """해외 9개 나라: 후보 중 시가총액 상위 50개씩"""
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor
    from intl_candidates import candidates

    import time

    def one(item):
        t, hint = item
        info = {}
        for k in range(3):   # 야후가 잠깐 막으면 쉬었다가 다시
            try:
                time.sleep(0.4)
                info = yf.Ticker(t).info or {}
                if info.get("marketCap"):
                    break
            except Exception:
                time.sleep(5 * (k + 1))
        cap = float(info.get("marketCap") or 0)
        name = str(info.get("shortName") or info.get("longName") or t).strip()
        ind = str(info.get("industry") or "")
        theme = hint or next((th for key, th in INDUSTRY_THEME if key in ind), "")
        cat = SECTOR_KO.get(str(info.get("sector") or ""), str(info.get("sector") or "기타"))
        return {"ticker": t, "name": name, "cap": cap, "theme": theme, "category": cat}

    out = {}
    for mk, items in candidates().items():
        with ThreadPoolExecutor(max_workers=2) as ex:
            res = [r for r in ex.map(one, items) if r["cap"] > 0]
        time.sleep(5)
        res.sort(key=lambda r: r["cap"], reverse=True)
        miss = [t for t, _ in items if t not in {r["ticker"] for r in res}]
        print(f"{mk}: 후보 {len(items)}개 중 시가총액 받은 곳 {len(res)}개" + (f" (못 받음: {' '.join(miss)})" if miss else ""))
        out[mk] = res[:INTL_TOP]
    return out


def main():
    rows = load()
    have = {r["ticker"] for r in rows}
    # 코스피·코스닥을 반대로 적은 경우도 같은 회사로 봄
    have_codes = {t.split(".")[0] for t in have}
    added = []

    try:
        for i, x in enumerate(kr_top(), 1):
            if x["ticker"].split(".")[0] in have_codes:
                continue
            rows.append({"ticker": x["ticker"], "name": x["name"], "market": "KR",
                         "category": f"{x['category']} 시총 {i}위", "theme": kr_theme(x["name"])})
            have_codes.add(x["ticker"].split(".")[0])
            added.append(f"🇰🇷 {x['name']}")
    except Exception as e:
        print("한국 순위 업데이트 실패:", e)

    try:
        for x in us_top():
            if x["ticker"] in have:
                continue
            rows.append({"ticker": x["ticker"], "name": x["name"], "market": "US",
                         "category": x["category"], "theme": x["theme"]})
            have.add(x["ticker"])
            added.append(f"🇺🇸 {x['name']}")
    except Exception as e:
        print("미국 순위 업데이트 실패:", e)

    try:
        for mk, lst in intl_top().items():
            for x in lst:
                if x["ticker"] in have:
                    continue
                rows.append({"ticker": x["ticker"], "name": x["name"], "market": mk,
                             "category": x["category"], "theme": x["theme"]})
                have.add(x["ticker"])
                added.append(f"{mk} {x['name']}")
    except Exception as e:
        print("해외 순위 업데이트 실패:", e)

    save(rows)
    print(f"새로 넣은 회사 {len(added)}개 → 전체 {len(rows)}개")
    for a in added:
        print("  +", a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
