# Текущие цены (last/mid) по всем бессрочным контрактам 7 бирж -> prices.json {ex: {sym: price}}; для сверки множителя/«один актив» (TK-509 r4). Запуск на srv: python3 -I fetch_prices.py
import json,urllib.request
def g(u,data=None):
    r=urllib.request.Request(u,data=data,headers={'User-Agent':'Mozilla/5.0','Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(r,timeout=40))
P={}
def safe(k,f):
    try: P[k]=f()
    except Exception as e: P[k]={}; print('ERR',k,e)
safe('binance_futures',lambda:{x['symbol']:float(x['price']) for x in g('https://fapi.binance.com/fapi/v1/ticker/price')})
safe('aster_futures',lambda:{x['symbol']:float(x['price']) for x in g('https://fapi.asterdex.com/fapi/v1/ticker/price')})
safe('bybit_futures',lambda:{x['symbol']:float(x['lastPrice']) for x in g('https://api.bybit.com/v5/market/tickers?category=linear')['result']['list'] if x['lastPrice']})
safe('okx_futures',lambda:{x['instId']:float(x['last']) for x in g('https://www.okx.com/api/v5/market/tickers?instType=SWAP')['data'] if x['last']})
safe('bitget_futures',lambda:{x['symbol']:float(x['lastPr']) for x in g('https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES')['data'] if x['lastPr']})
safe('gate_futures',lambda:{x['contract']:float(x['last']) for x in g('https://api.gateio.ws/api/v4/futures/usdt/tickers') if x['last']})
def hl():
    d={}
    for dex in ['','xyz','flx','km','cash','para','io','vntl','hyna']:
        try:
            m=g('https://api.hyperliquid.xyz/info',json.dumps({'type':'allMids','dex':dex}).encode() if dex else json.dumps({'type':'allMids'}).encode())
            d.update({k:float(v) for k,v in m.items() if not k.startswith('@')})
        except Exception as e: print('ERR hl',dex,e)
    return d
safe('hyperliquid_futures',hl)
json.dump(P,open('/root/p40u/prices.json','w'))
print({k:len(v) for k,v in P.items()})
