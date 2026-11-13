"""임시 점검용: 이상해 보이는 숫자들의 원본 자료를 찍어 봅니다 (봇 실행과 무관)."""
import json
import requests
import pandas as pd
import yfinance as yf

H = {"User-Agent": "Mozilla/5.0"}

print("=== 1) 네이버 분기 실적 구조 ===")
for code in ["078930", "005930", "000660", "036570", "259960"]:
    try:
        r = requests.get(f"https://m.stock.naver.com/api/stock/{code}/finance/quarter", headers=H, timeout=15)
        j = r.json()
        fi = j.get("financeInfo", j)
        if code == "078930":
            print(json.dumps(j, ensure_ascii=False)[:2500])
        titles = fi.get("trTitleList", [])
        print(code, [(t.get("key"), t.get("isConsensus")) for t in titles])
        for row in fi.get("rowList", []):
            if "순이익" in row.get("title", ""):
                print("  ", row["title"], {k: v.get("value") for k, v in row.get("columns", {}).items()})
    except Exception as e:
        print(code, "실패", e)

print("=== 2) 야후 이익 성장 ===")
for t in ["078930.KS", "005930.KS", "000660.KS", "036570.KS", "JNJ", "PSX", "SNDK", "7203.T", "0700.HK"]:
    try:
        tk = yf.Ticker(t)
        i = tk.info or {}
        q = tk.quarterly_income_stmt
        ni = q.loc["Net Income"] if q is not None and "Net Income" in q.index else None
        print(t, i.get("earningsGrowth"), i.get("earningsQuarterlyGrowth"), i.get("mostRecentQuarter"),
              None if ni is None else {str(k.date()): v for k, v in ni.head(6).items()})
    except Exception as e:
        print(t, "실패", e)

print("=== 3) 가격 급변(분할 의심) ===")
for t in ["1545.T", "2558.T", "1321.T", "1306.T", "512480.SS", "011070.KS", "001440.KS", "006800.KS",
          "056080.KQ", "MRNA", "036930.KQ", "395270.KS", "069500.KS"]:
    try:
        h = yf.download(t, period="2y", auto_adjust=False, progress=False)
        c = h["Close"].squeeze().dropna()
        ch = c.pct_change()
        big = ch[ch.abs() > 0.35]
        print(t, "최고", c.idxmax().date(), round(float(c.max()), 2), "지금", round(float(c.iloc[-1]), 2),
              "급변", {str(k.date()): round(float(v), 3) for k, v in big.items()})
        a = yf.Ticker(t).splits
        if a is not None and len(a):
            print("   분할기록", {str(k.date()): float(v) for k, v in a.tail(3).items()})
    except Exception as e:
        print(t, "실패", e)

print("=== 4) 네이버 일별 가격(한국) 최고가 ===")
import crosscheck
for code in ["011070", "001440", "006800", "069500"]:
    try:
        s2 = crosscheck.naver_series(code)
        print(code, "최고", s2.idxmax().date(), s2.max(), "지금", s2.iloc[-1], len(s2))
    except Exception as e:
        print(code, "실패", e)
