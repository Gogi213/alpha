#!/usr/bin/env python3
"""TK-401: ряды середины DEX для НОВЫХ Solana-mint TK-393 (свопы swaps3 tk401/s3/*-swaps.csv (пара vault'ов), 15-22.10; дубли зеркал buy/sell по (sig,coin,qty_m,qty_q) сняты; AGG=med|last — середина секунды (Судья 25.10 21:45)) — цена исполнения в единицах квоты; дельта В-213: монеты без кривой TK-392.
Квота монеты = самая частая из WSOL/USDC/USDT (свопы других квот отброшены). WSOL -> нога SOL (середина × сделки Gate спот SOL_USDT в tk392-event.py), стейбл -> без ноги.
Чистка как tk392-mid-sol: usd>=5 (WSOL — по медиане SOL_USDT Gate), выброс |p/медиана ±30 соседей - 1|>10 %; <200 свопов и медиана 0,98-1,02 $ (стейблы) — пропуск.
Запуск на calc: tk401-mid-sol.py OUTDIR -> OUTDIR/mid-sol/COIN.csv.gz (ts,slot,txidx,price), OUTDIR/list-sol.txt (монета и нога), OUTDIR/skip-sol.csv."""
import csv, glob, gzip, os, sys, collections, statistics
AGG = os.environ.get('AGG', 'med')
csv.field_size_limit(1 << 30)
out = sys.argv[1]; os.makedirs(out + f'/mid-sol3-{AGG}', exist_ok=True); K = '/data/tk0380/'; G = '/srv/alpha-sda/tk372-gate/raw/'
done = set()
for d in os.listdir(K + 'tk392'):
    if d.startswith('curve') and os.path.isdir(K + 'tk392/' + d):
        done |= {f.split('-curve')[0] for f in os.listdir(K + 'tk392/' + d) if f.endswith('-curve.csv')}
sd = G + 'spot/SOL_USDT'; px = []
for r in csv.DictReader(gzip.open(f'{sd}/{sorted(os.listdir(sd))[0]}', 'rt')): px.append(float(r['price']))
sol_ref = statistics.median(px)
SW = collections.defaultdict(lambda: collections.defaultdict(list)); seen = set(); ndup = 0
for p in sorted(glob.glob(out + '/s3/*-swaps.csv')):
    for r in csv.DictReader(open(p)):
        if r['quote'] not in ('WSOL', 'USDC', 'USDT'): continue
        k = (r['sig'], r['coin'], r['qty_m'], r['qty_q'])
        if k in seen: ndup += 1; continue
        seen.add(k)
        try: pq = float(r['price_q']); q = float(r['qty_q'])
        except ValueError: continue
        if pq <= 0 or q * (sol_ref if r['quote'] == 'WSOL' else 1) < 5: continue
        SW[r['coin']][r['quote']].append((int(r['ts']), int(r['slot']), int(r['txidx']), pq))
skip = {}; lst = []
for c, byq in sorted(SW.items()):
    if c in done: skip[c] = 'есть кривая TK-392'; continue
    q = max(byq, key=lambda k: len(byq[k])); rows = sorted(byq[q])
    if len(rows) < 200: skip[c] = f'свопов {len(rows)} < 200 ({q})'; continue
    if not any(os.path.isdir(f'{G}{k}/{c}_USDT') for k in ('spot', 'fut')): skip[c] = 'нет данных Gate'; continue
    ps = [r[3] for r in rows]
    if q != 'WSOL' and 0.98 <= statistics.median(ps) <= 1.02: skip[c] = 'стейбл'; continue
    keep = []
    for i, r in enumerate(rows):
        nb = ps[max(0, i - 30):i] + ps[i + 1:i + 31]
        if nb and abs(r[3] / statistics.median(nb) - 1) > 0.1: continue
        keep.append(r)
    with gzip.open(f'{out}/mid-sol/{c}.csv.gz', 'wt') as f:
        for ts, s, t, p in keep: f.write(f'{ts},{s},{t},{p:.12g}\n')
    lst.append(f"{c} {'SOL' if q == 'WSOL' else ''}")
open(out + f'/list-sol3-{AGG}.txt', 'w').write(''.join(l + '\n' for l in lst))
with open(out + f'/skip-sol3-{AGG}.csv', 'w', newline='') as f:
    w = csv.writer(f); w.writerow(['coin', 'why']); w.writerows(sorted(skip.items()))
print(len(lst), 'монет Solana; пропущено', len(skip), '; дублей снято', ndup)
