import json,urllib.request,time,urllib.parse
UA="Mozilla/5.0 alpha-audit"
def get(u):
    for i in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":UA}),timeout=30))
        except Exception as e:
            err=str(e); time.sleep(1.5)
    return {"_err":err}
need=json.load(open("/data/tk584/need-candles.json")); out={}
for ex,syms in need.items():
    out[ex]={}
    for s in syms:
        q=urllib.parse.quote(s)
        if ex=="bybit":
            d=get(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={q}&interval=60&limit=200")
            rows=[(int(r[0]),float(r[6])) for r in d.get("result",{}).get("list",[])] if "result" in d else d
        elif ex=="okx":
            d=get(f"https://www.okx.com/api/v5/market/history-candles?instId={q}&bar=1H&limit=100")
            rows=[(int(r[0]),float(r[7])) for r in d["data"]] if "data" in d else d
        elif ex=="gate":
            d=get(f"https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={q}&interval=1h&limit=120")
            rows=[(int(r["t"])*1000,float(r["sum"])) for r in d] if isinstance(d,list) else d
        elif ex=="bingx":
            d=get(f"https://open-api.bingx.com/openApi/swap/v3/quote/klines?symbol={q}&interval=1h&limit=120")
            rows=[(int(r["time"]),float(r["volume"])*float(r["close"])) for r in d["data"]] if isinstance(d,dict) and d.get("data") else d
        elif ex=="binance":
            d=get(f"https://fapi.binance.com/fapi/v1/klines?symbol={q}&interval=1h&limit=100")
            rows=[(int(r[0]),float(r[7])) for r in d] if isinstance(d,list) else d
        out[ex][s]=rows
        time.sleep(0.12)
    json.dump(out,open("/data/tk584/candles.json","w"))
    print(ex,len(syms),sum(1 for v in out[ex].values() if isinstance(v,list) and v),"with candles")
