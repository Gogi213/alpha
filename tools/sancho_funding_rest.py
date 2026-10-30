"""TK-454: сверка фандинга сделок слоя 4 с REST /fapi/v1/fundingRate (data.binance.vision считался в sancho_trades_funding.py).
Запуск: python tools/sancho_funding_rest.py <funding.csv> <out.csv>. Сумма ставок в (t_entry, t_exit] по REST, в bps, со знаком как в funding.csv."""
import csv, sys, json, urllib.request
src, out = sys.argv[1], sys.argv[2]
res = []
for r in csv.DictReader(open(src, encoding='utf-8')):
    t0, t1 = int(r['t_entry_ms']), int(r['t_exit_ms']); rates = []; cur = t0 + 1
    while True:
        u = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={r['symbol']}&startTime={cur}&endTime={t1}&limit=1000"
        a = json.load(urllib.request.urlopen(u, timeout=30))
        rates += [(x['fundingTime'], float(x['fundingRate'])) for x in a]
        if len(a) < 1000: break
        cur = a[-1]['fundingTime'] + 1
    n = [x for t, x in rates if t0 < t <= t1]
    sign = -1 if r['dir'] == 'long' else 1
    bps = round(sign * sum(n) * 1e4, 2)
    res.append({'trade_id': r['trade_id'], 'symbol': r['symbol'], 'n_rest': len(n), 'n_vision': r['n_funding'], 'bps_rest': bps, 'bps_vision': r['funding_bps_received'],
                'совпало': int(len(n) == int(r['n_funding']) and abs(bps - float(r['funding_bps_received'])) <= 0.01)})
w = csv.DictWriter(open(out, 'w', newline='', encoding='utf-8'), fieldnames=list(res[0])); w.writeheader(); w.writerows(res)
for x in res: print(x)
