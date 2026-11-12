#!/usr/bin/env python3
"""TK-402 П-28 v3.1 (B11): история выплат фандинга Gate фьюч и Bybit фьюч 14.10–23.10 (публичные REST) для ВСЕХ монет stage1.tsv (459) -> OUT.json
{coin: {"g": [[ts_s, rate], ...], "b": [[ts_s, rate], ...], "g_int": [...], "b_int": [...]}}; символ Bybit — из stage1.tsv (bybit_fut), Gate — COIN_USDT.
Bybit — постранично назад по endTime (интервал 1 ч даёт >200 выплат). Пустой ряд = ошибка: монета в списке `empty`, rc=1 (не молчаливый 0).
Запуск: tk402-funding-fetch.py /data/tk0402/stage1.tsv OUT.json"""
import json, urllib.request, sys, time, csv
from concurrent.futures import ThreadPoolExecutor
T0 = 1792022400 - 86400; T1 = 1792022400 + 8 * 86400
def get(u):
    err = None
    for k in range(5):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=20))
        except Exception as e: err = e; time.sleep(1 + k)
    raise RuntimeError(f'{u}: {err}')
def gate(c):
    r = get(f'https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract={c}_USDT&from={T0}&to={T1}&limit=1000')
    return sorted([int(x['t']), float(x['r'])] for x in r)
def bybit(sym):
    out = {}; end = T1 * 1000
    while True:
        r = get(f'https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}&startTime={T0*1000}&endTime={end}&limit=200')['result']['list']
        if not r: break
        for x in r: out[int(x['fundingRateTimestamp']) // 1000] = float(x['fundingRate'])
        mn = min(int(x['fundingRateTimestamp']) for x in r)
        if len(r) < 200 or mn <= T0 * 1000: break
        end = mn - 1
    return sorted([t, v] for t, v in out.items())
def one(row):
    c = row['coin']; res = dict(g=[], b=[], err=None)
    try:
        res['g'] = gate(c)
        if row['bybit_fut']: res['b'] = bybit(row['bybit_fut'])
    except Exception as e: res['err'] = repr(e)
    return c, res
rows = list(csv.DictReader(open(sys.argv[1], encoding='utf-8'), delimiter='\t'))
rows = [r for r in rows if r['bybit_fut']]
with ThreadPoolExecutor(6) as ex: out = dict(ex.map(one, rows))
bad = {c: ('err' if v['err'] else ('gate_empty' if not v['g'] else 'bybit_empty')) for c, v in out.items() if v['err'] or not v['g'] or not v['b']}
json.dump(out, open(sys.argv[2], 'w'))
print('coins', len(out), 'empty/err', len(bad), bad, flush=True)
sys.exit(1 if bad else 0)
