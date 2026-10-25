#!/usr/bin/env python3
"""TK-394 возврат (Судья 06:52): история выплат фандинга Gate фьюч и Bybit фьюч за 14.10–22.10 (публичные REST) по монетам из списка -> OUT.json
{coin: {"g": [[ts_s, rate], ...], "b": [[ts_s, rate], ...]}}. Запуск: tk394-funding-fetch.py LIST.txt OUT.json"""
import json, urllib.request, sys, time
T0 = 1792022400 - 86400; T1 = 1792022400 + 8 * 86400
def get(u):
    for _ in range(3):
        try: return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=20))
        except Exception as e: err = e; time.sleep(1)
    print('FAIL', u, err, flush=True); return None
out = {}
for c in open(sys.argv[1]).read().split():
    g = get(f'https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract={c}_USDT&from={T0}&to={T1}&limit=1000')
    b = None
    for pre in ('', '1000', '1000000', '10000'):
        r = get(f'https://api.bybit.com/v5/market/funding/history?category=linear&symbol={pre}{c}USDT&startTime={T0*1000}&endTime={T1*1000}&limit=200')
        if r and r['result']['list']: b = r['result']['list']; break
    out[c] = dict(g=sorted([int(x['t']), float(x['r'])] for x in (g or [])), b=sorted([int(x['fundingRateTimestamp']) // 1000, float(x['fundingRate'])] for x in (b or [])))
    print(c, len(out[c]['g']), len(out[c]['b']), flush=True)
json.dump(out, open(sys.argv[2], 'w'))
