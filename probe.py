import requests, pandas as pd, yfinance as yf, crosscheck
H={"User-Agent":"Mozilla/5.0"}
want={"2026-11-13":206000,"2026-11-18":196400,"2026-11-19":197400,"2026-11-20":183100,"2026-11-23":177500}
print("기준(뉴스 정규장 종가):",want)
s=crosscheck.naver_series("005930"); print("네이버 siseJson:", {d:s.get(pd.Timestamp(d)) for d in want})
for u in ["https://api.stock.naver.com/chart/domestic/item/005930/day?startDateTime=202611100000&endDateTime=202611240000",
          "https://m.stock.naver.com/api/stock/005930/price?pageSize=10&page=1",
          "https://m.stock.naver.com/api/stock/005930/basic"]:
    try:
        j=requests.get(u,headers=H,timeout=15).json()
        print(u.split('?')[0], str(j)[:1500])
    except Exception as e: print(u,e)
for t in ["005930.KS","000660.KS"]:
    h=yf.download(t,start="2026-11-10",auto_adjust=False,progress=False)
    print(t,"야후:",{str(k.date()):float(v) for k,v in h["Close"].squeeze().items()})
s=crosscheck.naver_series("000660"); print("하이닉스 siseJson:", {str(k.date()):v for k,v in s.tail(8).items()})
