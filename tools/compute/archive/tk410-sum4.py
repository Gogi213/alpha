#!/usr/bin/env python3
"""TK-410 rest6: сводка maxT (Westfall-Young). Вход: каталог с *-curve3.csv и *-maxT.npy. Выход: sum4-coins.csv, sum4-cells.csv (cwd). Семья = ВСЕ клетки монеты (n>=30, n_days>=3, exc3 определён), без отбора по ctrl2; глобально = max по монетам на перестановку."""
import sys, glob, os, csv, numpy as np
d = sys.argv[1]; coins = []; allc = []; mx = {}
for f in sorted(glob.glob(f'{d}/*-curve3.csv')):
    coin = os.path.basename(f).split('-')[0]
    r = [x for x in csv.DictReader(open(f, newline='')) if x.get('pw_exc') not in (None, '', 'nan')]
    mx[coin] = np.load(f'{d}/{coin}-maxT.npy'); allc += r
gm = np.max(np.stack([v[0] for v in mx.values()]), 0); gt = np.max(np.stack([v[1] for v in mx.values()]), 0); K = len(gm)
for coin in mx:
    r = [x for x in allc if x['coin'] == coin]
    if not r: print('пусто (нет клеток n>=30):', coin); continue
    coins.append(dict(coin=coin, cells=len(r), min_pw_exc=min(float(x['pw_exc']) for x in r), min_pw_t=min(float(x['pw_t']) for x in r),
                      fwer_pass_exc=sum(float(x['pw_exc']) < .05 for x in r), fwer_pass_t=sum(float(x['pw_t']) < .05 for x in r),
                      max_exc3=max(float(x['exc3']) for x in r), max_t=max(float(x['tstat']) for x in r)))
for x in allc:
    x['pg_exc'] = (1 + (gm >= float(x['exc3'])).sum()) / (K + 1); x['pg_t'] = (1 + (gt >= float(x['tstat'])).sum()) / (K + 1)
    x['econ'] = int(float(x['exc3']) > 2.6 and float(x['ci3_lo']) > 2.6)
def wr(fn, rows):
    with open(fn, 'w', newline='') as h:
        w = csv.DictWriter(h, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
wr('sum4-coins.csv', coins); wr('sum4-cells.csv', allc)
for c in coins: print(c)
print('K', K, 'cells', len(allc), 'glob pass exc', sum(float(x['pg_exc']) < .05 for x in allc), 'glob pass t', sum(float(x['pg_t']) < .05 for x in allc),
      'econ(exc3>2.6&ci3_lo>2.6)', sum(x['econ'] for x in allc), 'econ & global-pass', sum(x['econ'] and (float(x['pg_exc']) < .05 or float(x['pg_t']) < .05) for x in allc))
