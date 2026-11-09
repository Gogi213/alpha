import json,urllib.request,sys
env=dict(l.strip().split('=',1) for l in open('/root/.chft.env') if '=' in l); K=env['CRYPTOHFTDATA_API_KEY']
A='https://api.cryptohftdata.com'
def jget(u):
    r=urllib.request.Request(u,headers={'X-API-Key':K}); return json.load(urllib.request.urlopen(r,timeout=60))
out={}
for ex in ['binance_futures','bybit_futures','okx_futures','bitget_futures','gate_futures','aster_futures','hyperliquid_futures','bingx_futures','kraken_futures','lighter_futures']:
    out[ex]={}
    for t in ['orderbook','trades','mark_price']:
        try:
            d=jget(f'{A}/v1/symbols?exchange={ex}&data_type={t}'); s=d.get('symbols',d) if isinstance(d,dict) else d
            out[ex][t]=[x if isinstance(x,str) else x.get('symbol') for x in s]
        except Exception as e: out[ex][t]=None; print(ex,t,e,file=sys.stderr)
    print(ex,{t:(len(v) if v else v) for t,v in out[ex].items()},file=sys.stderr)
json.dump(out,open('/root/p40u-symbols.json','w'))
