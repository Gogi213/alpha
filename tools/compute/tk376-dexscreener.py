#!/usr/bin/env python3
"""TK-376 п.5: охват наших пулов по DexScreener (срез на момент запуска, не 17–21.10). Вход: coin-pools.json (net/dir -> [пулы]).
По пулу -> /latest/dex/pairs/{chain}/{addr} (токен монеты), затем /token-pairs/v1/{chain}/{token}: vol24 всех пулов vs наших. Выход: JSON/CSV."""
import sys, json, time, urllib.request, collections, csv
CH = {'ethereum': 'ethereum', 'base': 'base', 'bsc': 'bsc'}
def get(u):
    for k in range(4):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30))
        except Exception as e:
            time.sleep(2 + 3 * k)
    return None
import concurrent.futures as cf
cp = {k: v for k, v in json.load(open(sys.argv[1])).items() if v}
def one(item):
    key, pools = item
    net, d = key.split('/'); sym = d.split('-')[0]; ours = {p.lower() for p in pools}
    r = get(f'https://api.dexscreener.com/latest/dex/pairs/{CH[net]}/{pools[0]}')
    pr = (r or {}).get('pair') or ((r or {}).get('pairs') or [None])[0]
    if not pr: return dict(net=net, coin=d, sym=sym, status='no_pair')
    b, q = pr['baseToken'], pr['quoteToken']
    tok = b if b['symbol'].upper().lstrip('W') == sym.upper().lstrip('W') else (q if q['symbol'].upper() == sym.upper() else b)
    tp = get(f"https://api.dexscreener.com/token-pairs/v1/{CH[net]}/{tok['address']}")
    if tp is None: return dict(net=net, coin=d, sym=sym, status='no_tp', token=tok['address'])
    v = lambda x, k: (x.get(k) or {}).get('h24' if k == 'volume' else 'usd') or 0
    mine = [x for x in tp if x['pairAddress'].lower() in ours]
    return dict(net=net, coin=d, sym=sym, status='ok', token=tok['address'], tok_sym=tok['symbol'], npools=len(tp), nours=len(mine),
                vol_all=round(sum(v(x, 'volume') for x in tp)), vol_ours=round(sum(v(x, 'volume') for x in mine)),
                liq_all=round(sum(v(x, 'liquidity') for x in tp)), liq_ours=round(sum(v(x, 'liquidity') for x in mine)))
with cf.ThreadPoolExecutor(6) as ex: out = list(ex.map(one, cp.items()))
json.dump(out, open(sys.argv[2], 'w'), ensure_ascii=False)
print(collections.Counter(o['status'] for o in out))
