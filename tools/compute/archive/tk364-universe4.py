# TK-364 v4 (замечания Судьи 23.10): группа high = нестейблы рангов 6-50 CoinGecko БЕЗ стейблов/токенизированных фондов/
# производных (золото PAXG/XAUT — пометка gold=1, в ранг не входит); перп Bybit ищется по base и 1000/1000000-префиксу;
# токен пула — каноническая сеть/адрес из CoinGecko platforms по id (копии/обёртки с чужим адресом не берутся);
# пул считается только если ликвидность >= 50 тыс. Выход: docs/findings/tk364-universe-v4-high-2026-10-23.csv
import json,csv,time,urllib.request,sys
def g(u,t=60):
    for i in range(3):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u,headers={"User-Agent":"x"}),timeout=t))
        except Exception as e: err=e; time.sleep(4)
    print("FAIL",u,err,file=sys.stderr); return None
mk=g("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page=1") or []
STABLE_SYM={"usdt","usdc","usde","dai","usds","fdusd","usdd","pyusd","usd1","usdtb","susds","usdg","usyc","rlusd","usdy","buidl","eursafo","susde","usdt0","usdf","usd0","bfusd","usdc.e","ustb","jtrsy","tusd","usdp","gusd","frax","lusd","crvusd","eurc","eura","usdgo"}
STABLE_ID_PART=("usd-coin","tether","stable","treasury","money-market","tokenized","wrapped","staked","liquid-staking","bridged","restaked","fund")
GOLD={"PAXG","XAUT"}
def is_stable(c):
    s=c["symbol"].lower(); i=c["id"].lower()
    return s in STABLE_SYM or any(p in i for p in STABLE_ID_PART) or s in ("steth","wsteth","weth","wbtc","wbeth","weeth","cbbtc","bsc-usd","bsol","jitosol","reth","cbeth","msol")
ranked=[c for c in mk if not is_stable(c) and c["symbol"].upper() not in GOLD]
gold=[c for c in mk if c["symbol"].upper() in GOLD]
calib=[c["symbol"].upper() for c in ranked[:5]]
high=ranked[5:50]
perps={p["symbol"]:p for p in json.load(open("data/tk364/bybit-linear.json"))}
ALIAS={"SHIB":"SHIB1000USDT","PUMP":"PUMPFUNUSDT"}
def find_perp(sym):
    if sym in ALIAS and ALIAS[sym] in perps: return ALIAS[sym]
    for pre in ("","1000","10000","1000000"):
        k=pre+sym+"USDT"
        if k in perps: return k
    return ""
cgl=g("https://api.coingecko.com/api/v3/coins/list?include_platform=true",120) or []
plat={c["id"]:c.get("platforms") or {} for c in cgl}
CH={"ethereum":"ethereum","binance-smart-chain":"bsc","solana":"solana","base":"base","arbitrum-one":"arbitrum","polygon-pos":"polygon","tron":"tron","avalanche":"avalanche","optimistic-ethereum":"optimism"}
out=[]
for c in high+gold:
    sym=c["symbol"].upper(); pk=find_perp(sym); p=perps.get(pk)
    tot=0; best=("",0); toks=[]
    for ch,a in plat.get(c["id"],{}).items():
        if not a or ch not in CH: continue
        toks.append(CH[ch]+":"+a)
        d=g("https://api.dexscreener.com/latest/dex/tokens/"+a); time.sleep(0.3)
        for pr in (d or {}).get("pairs",[]) or []:
            if pr["baseToken"]["address"].lower()!=a.lower() or pr["chainId"]!=CH[ch]: continue
            if ((pr.get("liquidity") or {}).get("usd") or 0)<50000: continue
            v=(pr.get("volume") or {}).get("h24") or 0; tot+=v
            if v>best[1]: best=(pr["chainId"],v)
    out.append(dict(group="gold" if sym in GOLD else "high",rank=c["market_cap_rank"],symbol=sym,cg_id=c["id"],perp=pk,
      perp_turn24=round(float(p["turnover24h"])) if p else "",perp_oi_usd=round(float(p["openInterestValue"])) if p else "",
      cg_vol24=round(c["total_volume"]),dex_tok=round(tot),dex_chain=best[0],has_pool=int(tot>0 and bool(p)),canon_tokens=";".join(toks)))
cols=list(out[0]); w=csv.DictWriter(open("docs/findings/tk364-universe-v4-high-2026-10-23.csv","w",newline="",encoding="utf-8"),cols); w.writeheader(); w.writerows(out)
json.dump(dict(calib=calib,high=[c["symbol"].upper() for c in high],gold=[c["symbol"].upper() for c in gold]),open("docs/findings/tk364-groups-2026-10-23.json","w"))
print("calib",calib,"high",len(high),"perp",sum(1 for r in out if r["perp"]),"pool",sum(r["has_pool"] for r in out))
