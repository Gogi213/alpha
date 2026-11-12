# TK-364: перпы Bybit с ончейн-ликвидностью спота (DexScreener search, без ключей). Выход: data/tk364/universe.csv
import json,re,time,urllib.request,urllib.parse,csv,sys
def g(u):
    for i in range(3):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=30))
        except Exception as e: time.sleep(2)
    return None
lin=[x for x in json.load(open("data/tk364/bybit-linear.json")) if x["symbol"].endswith("USDT")]
spot={x["symbol"]:float(x["turnover24h"] or 0) for x in json.load(open("data/tk364/bybit-spot.json"))}
rows=[]
for x in lin:
    s=x["symbol"][:-4]
    mult=re.match(r"^(\d+)(.*)$",s)
    base=mult.group(2) if mult and mult.group(2) else s
    d=g("https://api.dexscreener.com/latest/dex/search?q="+urllib.parse.quote(base+" USDT")) 
    best=None;tot=0;nets={}
    for p in (d or {}).get("pairs",[]) or []:
        if p["baseToken"]["symbol"].upper()!=base.upper(): continue
        v=(p.get("volume") or {}).get("h24") or 0; l=(p.get("liquidity") or {}).get("usd") or 0
        if l<50000: continue
        tot+=v; nets[p["chainId"]]=nets.get(p["chainId"],0)+v
        if best is None or v>best[0]: best=(v,l,p["chainId"],p["dexId"],p["pairAddress"],p["baseToken"]["address"])
    rows.append([x["symbol"],base,float(x["turnover24h"] or 0),float(x["openInterestValue"] or 0),spot.get(x["symbol"],0),tot,
      max(nets,key=nets.get) if nets else "",best[1] if best else 0,best[3] if best else "",best[4] if best else "",best[5] if best else ""])
    time.sleep(0.22)
w=csv.writer(open("data/tk364/universe.csv","w",newline="",encoding="utf-8"))
w.writerow("symbol base perp_turn24 perp_oi_usd bybit_spot_turn24 dex_vol24 top_chain top_liq dex pair token".split()); w.writerows(rows)
print(len(rows))
