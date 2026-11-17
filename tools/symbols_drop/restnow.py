import json,urllib.request,time,datetime
UA="Mozilla/5.0 (X11; Linux x86_64) alpha-collector-audit"
def get(u):
    r=urllib.request.Request(u,headers={"User-Agent":UA})
    return json.load(urllib.request.urlopen(r,timeout=30))
out={"ts":int(time.time()*1000)}
# binance
info=get("https://fapi.binance.com/fapi/v1/exchangeInfo"); tk=get("https://fapi.binance.com/fapi/v1/ticker/24hr")
q={t["symbol"]:float(t["quoteVolume"]) for t in tk}
out["binance"]={s["symbol"]:{"status":s["status"],"ctype":s["contractType"],"quote":s["quoteAsset"],"qv":q.get(s["symbol"]),"onboard":s.get("onboardDate"),"delivery":s.get("deliveryDate")} for s in info["symbols"]}
# bybit
ins=[];cur=""
while True:
    p=get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000"+(("&cursor="+cur) if cur else ""))["result"]
    ins+=p["list"]; cur=p.get("nextPageCursor","")
    if not cur: break
tk=get("https://api.bybit.com/v5/market/tickers?category=linear")["result"]["list"]
q={t["symbol"]:float(t["turnover24h"]) for t in tk}
out["bybit"]={s["symbol"]:{"status":s["status"],"ctype":s["contractType"],"quote":s["quoteCoin"],"qv":q.get(s["symbol"]),"launch":s.get("launchTime"),"delivery":s.get("deliveryTime")} for s in ins}
# okx
ins=get("https://www.okx.com/api/v5/public/instruments?instType=SWAP")["data"]; tk=get("https://www.okx.com/api/v5/market/tickers?instType=SWAP")["data"]
q={t["instId"]:float(t["volCcy24h"])*float(t["last"]) for t in tk if t.get("volCcy24h") and t.get("last")}
out["okx"]={s["instId"]:{"status":s["state"],"ctype":s["ctType"],"quote":s["settleCcy"],"qv":q.get(s["instId"]),"listTime":s.get("listTime"),"expTime":s.get("expTime")} for s in ins}
# bingx
H="https://open-api.bingx.com/openApi/swap/v2/quote"
ct=get(H+"/contracts")["data"]; tk=get(H+"/ticker")["data"]
q={t["symbol"]:float(t["quoteVolume"]) for t in tk if t.get("quoteVolume") is not None}
out["bingx"]={c["symbol"]:{"status":c.get("status"),"quote":c.get("currency"),"qv":q.get(c["symbol"]),"apiOpen":c.get("apiStateOpen"),"apiClose":c.get("apiStateClose"),"launch":c.get("launchTime"),"off":c.get("offTime")} for c in ct}
# gate
G="https://api.gateio.ws/api/v4/futures/usdt"
ct=get(G+"/contracts"); tk=get(G+"/tickers")
q={t["contract"]:float(t["volume_24h_quote"]) for t in tk if t.get("volume_24h_quote") is not None}
out["gate"]={c["name"]:{"status":c.get("status"),"qv":q.get(c["name"]),"delisting":c.get("in_delisting"),"launch":c.get("launch_time"),"type":c.get("type")} for c in ct}
json.dump(out,open("/data/tk584/rest-now.json","w"))
print({k:len(v) for k,v in out.items() if k!="ts"}, datetime.datetime.fromtimestamp(out["ts"]/1000,datetime.UTC))
