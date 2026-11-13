"""임시 점검용: 고친 코드를 실제 자료에 돌려 봅니다 (봇 실행·알림과 무관)."""
import pandas as pd
import yfinance as yf
import pricecache
import probe_bot as stock_bot

print("=== 1) 한국 종목 네이버 '작년보다 번 돈' ===")
st = pd.read_csv("stocks.csv")
kr = [t for t in st.ticker if t.endswith((".KS", ".KQ"))]
res = {t: stock_bot.naver_growth(t) for t in kr}
print(f"한국 {len(kr)}개 중 값 있음 {sum(1 for g, q in res.values() if not pd.isna(g))}개, "
      f"분기 표시 있음 {sum(1 for g, q in res.values() if q)}개")
print("분기 분포", pd.Series([q for g, q in res.values()]).value_counts().to_dict())
for t in ["078930.KS", "005930.KS", "000660.KS", "036570.KS", "259960.KS", "035720.KS", "011070.KS"]:
    print(" ", t, res.get(t))
print("±300% 넘는 한국 종목", {t: (round(v[0], 2), v[1]) for t, v in res.items() if not pd.isna(v[0]) and abs(v[0]) > 3})

print("=== 2) 가격 튐 바로잡기 — ETF(1.6배 기준) ===")
etfs = open("probe_etfs.txt").read().split()
raw = yf.download(etfs, period="max", auto_adjust=False, progress=False, group_by="ticker", threads=True)
n = 0
for t in etfs:
    try:
        c = raw[t]["Close"].dropna()
        if len(c) < 3:
            continue
        f = pricecache.fix_jumps(c, 1.6, t)
        if len(f) != len(c) or abs(f.iloc[0] / c.iloc[0] - 1) > 1e-9:
            n += 1
            print(f"   → {t}: 1년 최고 대비 {f.iloc[-1] / f.iloc[-260:].max() - 1:+.1%} "
                  f"(고치기 전 {c.iloc[-1] / c.iloc[-260:].max() - 1:+.1%})")
    except Exception as e:
        print(t, "실패", e)
print("ETF 고친 종목 수", n)

print("=== 3) 가격 튐 바로잡기 — 개별종목(3배 기준) ===")
raw = yf.download(list(st.ticker), period="5y", auto_adjust=False, progress=False, group_by="ticker", threads=True)
n = 0
for t in st.ticker:
    try:
        c = raw[t]["Close"].dropna()
        if len(c) < 3:
            continue
        f = pricecache.fix_jumps(c, 3.0, t)
        if len(f) != len(c) or abs(f.iloc[0] / c.iloc[0] - 1) > 1e-9:
            n += 1
    except Exception:
        pass
print("개별종목 고친 종목 수", n)
