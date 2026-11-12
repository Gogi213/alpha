#!/usr/bin/env python3
"""TK-387: оценка tx/сутки пулов Solana по плотности подписей — 4 точки суток × 3 дня на пул, страница 100 подписей (signatures, asc).
argv: pools.csv. Печатает по пулу: tx/сут по дням, итого tx и кредитов (10/100 tx) за окно листинг..14.10. Ключ — env HELIUS_API_KEY."""
import csv, datetime as dt, json, os, sys, time, urllib.error, urllib.request
URL = 'https://mainnet.helius-rpc.com/?api-key=' + os.environ['HELIUS_API_KEY']
T0, T1 = 1788220800, 1792022400
req = 0
def page(pool, a):
    global req
    cfg = {'transactionDetails': 'signatures', 'sortOrder': 'asc', 'limit': 100, 'filters': {'blockTime': {'gte': a, 'lt': a + 86400}, 'status': 'succeeded'}}
    r = urllib.request.Request(URL, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'getTransactionsForAddress', 'params': [pool, cfg]}).encode(), {'Content-Type': 'application/json', 'User-Agent': 'alpha-research'})
    for att in range(12):
        try:
            d = json.loads(urllib.request.urlopen(r, timeout=90).read()); req += 1
            break
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(3 + att * 3)
    return [x['blockTime'] for x in d['result'].get('data', [])]
tot_all = 0
for r in csv.DictReader(open(sys.argv[1])):
    s = max(T0, int(dt.datetime.fromisoformat(r['start_day']).replace(tzinfo=dt.timezone.utc).timestamp()))
    days = list(range(s, T1, 86400)); n = len(days)
    pick = sorted({days[0], days[n // 2], days[-1]})
    est = []
    for d0 in pick:
        rates = []
        for h in (2, 8, 14, 20):
            t = page(r['pool'], d0 + h * 3600)
            if len(t) >= 2 and t[-1] > t[0]:
                rates.append((len(t) - 1) / (t[-1] - t[0]))
            elif len(t) < 100:
                rates.append(len(t) / 3600)  # меньше страницы за остаток суток — грубо по числу
        est.append(round(sum(rates) / len(rates) * 86400) if rates else 0)
    mean = sum(est) / len(est); total = mean * n; tot_all += total
    print(f"{r['coin']} дней={n} tx/сут(выборка)={est} итого≈{total/1e3:.0f}k tx ≈{total/10/1e3:.1f}k кр.")
print(f'ВСЕГО ≈{tot_all/1e6:.2f} млн tx ≈{tot_all/10/1e3:.0f}k кредитов, запросов пробы {req}')
