import requests, json
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"}
codes=["AAPL.O","KO","KO.N","NVDA.O","7203.T","0700.HK","SHEL.L","SAP.DE","600519.SS","300750.SZ","VNM.HM","BBCA.JK","D05.SI","RY.TO","RY"]
urls=["https://api.stock.naver.com/stock/{c}/basic",
      "https://api.stock.naver.com/chart/foreign/item/{c}/day?startDateTime=202610010000&endDateTime=202611102359",
      "https://api.stock.naver.com/chart/foreign/item/{c}?periodType=dayCandle"]
for c in codes:
    for u in urls:
        url=u.format(c=c)
        try:
            r=requests.get(url,headers=UA,timeout=10); t=r.text[:160].replace("\n"," ")
            print(r.status_code, url, "|", t)
        except Exception as e: print("ERR",url,e)
for u in ["https://stooq.com/q/d/l/?s=aapl.us&i=d","https://query1.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d"]:
    r=requests.get(u,headers=UA,timeout=10); print(r.status_code,u,"|",r.text[:200].replace("\n"," "))
