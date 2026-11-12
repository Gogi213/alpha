#!/usr/bin/env python3
"""TK-386 шаг 1 (п.1 спецификации аудита TK-384): инструменты Binance/Bybit/OKX (спот+фьюч) по API на дату счёта -> csv.
Запуск: tk386-listings.py OUT.csv"""
import sys, json, csv, urllib.request, datetime
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'user-agent': 'curl/8.0'}), timeout=30))
rows = set()
def add(ex, kind, base): rows.add((ex, kind, base.upper()))
for ex, kind, u, f in [
    ('binance', 'spot', 'https://api.binance.com/api/v3/exchangeInfo', lambda d: [s['baseAsset'] for s in d['symbols'] if s['status'] == 'TRADING']),
    ('binance', 'fut', 'https://fapi.binance.com/fapi/v1/exchangeInfo', lambda d: [s['baseAsset'] for s in d['symbols'] if s['status'] == 'TRADING']),
    ('bybit', 'spot', 'https://api.bybit.com/v5/market/instruments-info?category=spot&limit=1000', lambda d: [s['baseCoin'] for s in d['result']['list'] if s['status'] == 'Trading']),
    ('bybit', 'fut', 'https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000', lambda d: [s['baseCoin'] for s in d['result']['list'] if s['status'] == 'Trading']),
    ('okx', 'spot', 'https://www.okx.com/api/v5/public/instruments?instType=SPOT', lambda d: [s['baseCcy'] for s in d['data'] if s['state'] == 'live']),
    ('okx', 'fut', 'https://www.okx.com/api/v5/public/instruments?instType=SWAP', lambda d: [s['ctValCcy'] or s['instId'].split('-')[0] for s in d['data'] if s['state'] == 'live']),
]:
    try:
        for b in f(get(u)): add(ex, kind, b)
    except Exception as e: print('FAIL', ex, kind, e, file=sys.stderr)
# bybit linear: cursor
try:
    d = get('https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000'); cur = d['result'].get('nextPageCursor')
    while cur:
        d = get('https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000&cursor=' + cur)
        for s in d['result']['list']:
            if s['status'] == 'Trading': add('bybit', 'fut', s['baseCoin'])
        cur = d['result'].get('nextPageCursor')
except Exception as e: print('FAIL bybit cursor', e, file=sys.stderr)
with open(sys.argv[1], 'w', newline='', encoding='utf-8') as fh:
    w = csv.writer(fh); w.writerow(['биржа', 'рынок', 'база', 'дата_UTC'])
    dt = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='minutes')
    for r in sorted(rows): w.writerow(list(r) + [dt])
import collections; print(collections.Counter((e, k) for e, k, _ in rows))
