#!/usr/bin/env python3
"""TK-392: середины всех главных v3-пулов (квота — стейбл) за один проход по частям SQD.
Запуск: tk392-mid-multi.py PICK.csv(coin,chain,pool,token,quote_addr) CHAIN OUTDIR  — нужен grep -F -i -f по пулам (делает tk392-run.sh).
stdin: строки сырого SQD (уже отфильтрованные grep) одной части; stdout: coin,ts,block,logidx,mid  (mid как в tk392-mid.py)."""
import sys, json, csv
pick, chain = sys.argv[1:3]
P = {}
for r in csv.DictReader(open(pick)):
    if r['chain'] == chain: P[r['pool'].lower()] = (r['coin'], r['token'].lower() < r['quote_addr'].lower())
w = sys.stdout
for line in sys.stdin:
    b = json.loads(line); h = b['header']
    for lg in b.get('logs', []):
        a = lg['address'].lower()
        if a not in P: continue
        d = lg['data'][2:]
        if len(d) < 5 * 64: continue
        sp = int(d[128:192], 16)
        if sp == 0: continue
        px = (sp / 2 ** 96) ** 2
        coin, t0 = P[a]
        w.write('%s,%d,%d,%d,%.12g\n' % (coin, h['timestamp'], h['number'], lg['logIndex'], px if t0 else 1 / px))
