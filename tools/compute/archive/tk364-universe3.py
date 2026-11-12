# TK-364 v3 (CEO 00:05: + хайкапы; группы calib = топ-5 нестейблов + BTC/ETH/SOL/XRP, high = ранги 6-50, mid = остальное). Основа v2: к universe.csv добавляет спот Binance/Upbit/OKX/Bitget, проверку контракта (CoinGecko platforms),
# суммарный DEX по контракту (DexScreener tokens/{addr}), список исключённых мажоров. Выход: docs/findings/tk364-universe-v3-2026-10-23.csv
import json,csv,time,urllib.request,sys
def g(u,t=60):
    for i in range(3):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=t))
        except Exception as e: err=e; time.sleep(3)
    print("FAIL",u,err,file=sys.stderr); return None
rows=list(csv.DictReader(open("data/tk364/universe.csv",encoding="utf-8")))
# мажоры: топ-20 CoinGecko по капитализации (стейблы не считаем, берём первые 20 нестейблов) + BTC/ETH/SOL/XRP
mk=g("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=70&page=1") or []
stable={"usdt","usdc","usde","dai","usds","fdusd","usdd","pyusd","usd1","usdtb","susds","steth","wsteth","weth","wbtc","wbeth","weeth","bsc-usd","bsol","cbbtc","susde","usdt0","usdf","jitosol","reth","cbeth"}
ranked=[]
for c in mk:
    if c["symbol"].lower() in stable: continue
    ranked.append(c["symbol"].upper())
calib=ranked[:5]+[x for x in ("BTC","ETH","SOL","XRP") if x not in ranked[:5]]
high=[x for x in ranked[5:50] if x not in calib]
majors=calib
cg=g("https://api.coingecko.com/api/v3/coins/list?include_platform=true",120) or []
by_addr={}; by_sym={}
for c in cg:
    by_sym.setdefault(c["symbol"].upper(),[]).append(c)
    for ch,a in (c.get("platforms") or {}).items():
        if a: by_addr.setdefault(a.lower(),[]).append((c,ch))
# CEX споты (24ч, в USD)
cex={"binance":{},"upbit":{},"okx":{},"bitget":{}}
for t in g("https://api.binance.com/api/v3/ticker/24hr") or []:
    for q in ("USDT","USDC","FDUSD"):
        if t["symbol"].endswith(q): cex["binance"][t["symbol"][:-len(q)]]=cex["binance"].get(t["symbol"][:-len(q)],0)+float(t["quoteVolume"])
mk_up=[m["market"] for m in g("https://api.upbit.com/v1/market/all") or []]
tick=[]
for i in range(0,len(mk_up),100): tick+=g("https://api.upbit.com/v1/ticker?markets="+",".join(mk_up[i:i+100])) or []
krw=next((t["trade_price"] for t in tick if t["market"]=="KRW-USDT"),1450.0)
for t in tick:
    q,b=t["market"].split("-")
    if q=="KRW": cex["upbit"][b]=cex["upbit"].get(b,0)+t["acc_trade_price_24h"]/krw
    elif q=="USDT": cex["upbit"][b]=cex["upbit"].get(b,0)+t["acc_trade_price_24h"]
for t in (g("https://www.okx.com/api/v5/market/tickers?instType=SPOT") or {}).get("data",[]):
    b,q=t["instId"].split("-")
    if q in("USDT","USDC"): cex["okx"][b]=cex["okx"].get(b,0)+float(t["volCcy24h"] or 0)
for t in (g("https://api.bitget.com/api/v2/spot/market/tickers") or {}).get("data",[]):
    if t["symbol"].endswith("USDT"): cex["bitget"][t["symbol"][:-4]]=float(t.get("usdtVolume") or 0)
print({k:len(v) for k,v in cex.items()},"krw",krw,"majors",majors,file=sys.stderr)
out=[]
for r in rows:
    if float(r["dex_vol24"] or 0)<=0 or r["base"].upper() in calib: continue
    b=r["base"]; addr=r["token"]
    d=g("https://api.dexscreener.com/latest/dex/tokens/"+addr) if addr else None; time.sleep(0.25)
    tot=0; chains={}
    for p in (d or {}).get("pairs",[]) or []:
        if p["baseToken"]["address"].lower()!=addr.lower(): continue
        if ((p.get("liquidity") or {}).get("usd") or 0)<50000: continue
        v=(p.get("volume") or {}).get("h24") or 0; tot+=v; chains[p["chainId"]]=chains.get(p["chainId"],0)+v
    m=by_addr.get(addr.lower(),[])
    cgid=m[0][0]["id"] if m else ""; cgname=m[0][0]["name"] if m else ""
    wrapped=any(w in (cgid+" "+cgname).lower() for w in ("wrapped","bridged","peg","bsc-","tokenized","ondo"))
    same=len(by_sym.get(b.upper(),[]))
    cx={k:cex[k].get(b,0)+cex[k].get("1000"+b,0) for k in cex}
    cexsum=sum(cx.values())+float(r["bybit_spot_turn24"])
    lead=max([("dex:"+(max(chains,key=chains.get) if chains else r["top_chain"]),tot)]+[(k,v) for k,v in cx.items()]+[("bybit_spot",float(r["bybit_spot_turn24"]))],key=lambda z:z[1])[0]
    out.append(dict(group="high" if b.upper() in high else "mid",symbol=r["symbol"],base=b,perp_turn24=r["perp_turn24"],perp_oi_usd=r["perp_oi_usd"],bybit_spot=r["bybit_spot_turn24"],
      binance=round(cx["binance"]),upbit=round(cx["upbit"]),okx=round(cx["okx"]),bitget=round(cx["bitget"]),dex_tok=round(tot),dex_chain=max(chains,key=chains.get) if chains else "",
      cex_sum=round(cexsum),lead=lead,cg_id=cgid,cg_wrapped=int(wrapped),cg_same_symbol=same,token=addr))
cols=list(out[0]); w=csv.DictWriter(open("docs/findings/tk364-universe-v3-2026-10-23.csv","w",newline="",encoding="utf-8"),cols); w.writeheader(); w.writerows(out)
json.dump(dict(calib=calib,high=high),open("docs/findings/tk364-groups-2026-10-23.json","w"))
print(len(out))
