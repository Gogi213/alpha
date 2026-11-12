#!/usr/bin/env python3
"""TK-394 возврат 2: фандинг Gate фьюч и Bybit фьюч за 15–21.10 (публичные REST) по 6 монетам -> docs/findings/tk394-funding-2026-10-25.csv.
Столбцы: coin, gate_n, gate_sum_bp_per_day, bybit_n, bybit_sum_bp_per_day, diff_bp_per_day (Gate-Bybit). Интервалы фандинга менялись в неделе (n записей), поэтому сумма ставок за неделю / 7, не нормировка на 8 ч."""
import json, urllib.request, sys, csv
T0 = 1792022400; T1 = T0 + 7 * 86400
def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=20))
rows = []
for c in ['ERA', 'IOST', 'DOOD', 'BMT', 'FLOCK', 'WAL']:
    g = get(f'https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract={c}_USDT&from={T0}&to={T1}&limit=1000')
    b = get(f'https://api.bybit.com/v5/market/funding/history?category=linear&symbol={c}USDT&startTime={T0*1000}&endTime={T1*1000}&limit=200')['result']['list']
    gr = [float(x['r']) for x in g]; br = [float(x['fundingRate']) for x in b]
    g7 = sum(gr) * 1e4 / 7; b7 = sum(br) * 1e4 / 7
    rows.append([c, len(gr), round(g7, 2), len(br), round(b7, 2), round(g7 - b7, 2)])
    print(rows[-1], flush=True)
with open('docs/findings/tk394-funding-2026-10-25.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['coin', 'gate_n', 'gate_sum_bp_per_day', 'bybit_n', 'bybit_sum_bp_per_day', 'diff_bp_per_day']); w.writerows(rows)
