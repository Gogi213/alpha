#!/usr/bin/env python3
"""TK-394 П-28 шаг 2: свод step2/*.npz. Отбор: эпизодов >= 30 (суммарно за неделю по клетке), net>0, net>0 в >= 5 из 7 суток.
p (блоковый бутстрап по 168 часам, односторонний, H0: среднее net <= 0) — только для прошедших отбор; Холм — по ВСЕМ клеткам N (кроме отбракованных по эфф. N: как в П-28 §7).
Запуск: tk394-step2-agg.py STEP2DIR OUT.csv [B]   Фандинг в этом проходе НЕ учтён (число пересечений границ выплат — столбцы fcross1/fcross2)."""
import sys, os, json, glob
import numpy as np
FEE = {'GF': 5.0, 'BF': 5.5, 'BS': 10.0}; DISC = 0.65
d = sys.argv[1]; out = sys.argv[2]; B = int(sys.argv[3]) if len(sys.argv) > 3 else 200000
rng = np.random.default_rng(394)
rows = []; ncell_all = 0; sel_stat = {}
for f in sorted(glob.glob(f'{d}/*.npz')):
    coin = os.path.basename(f)[:-4]; z = np.load(f); cells = json.load(open(f'{d}/{coin}.json'))
    S = z['sums']; C = z['cnts'].astype(float); E = z['eps']; X1 = z['cr1']; X2 = z['cr2']; DR = z['drops']
    for i, (pair, mode, anc, sc, k, Dl, T) in enumerate(cells):
        ncell_all += 1
        circ = 2 * (FEE['GF'] * (DISC if sc == 'disc' else 1.0) + (FEE['BF'] if pair == 'FF' else FEE['BS']))
        net = S[i] - C[i] * circ
        n = C[i].sum(); ne = int(E[i].sum())
        key = (pair, mode, anc, sc)
        st = sel_stat.setdefault(key, [0, 0, 0])      # клеток, с эфф.N>=30, прошли отбор
        st[0] += 1
        if ne < 30: continue
        st[1] += 1
        mean = net.sum() / n
        dn = net.reshape(7, 24).sum(1); dc = C[i].reshape(7, 24).sum(1)
        pos_days = int(np.sum((dn > 0) & (dc > 0)))
        gmean = S[i].sum() / n
        rows.append(dict(coin=coin, pair=pair, mode=mode, anchor=anc, scen=sc, k=k, delay_s=Dl, timeout_s=T, circle=round(circ, 2), n_trades=int(n), n_ep=ne,
                         gross_mean=round(gmean, 2), net_mean=round(mean, 2), pos_days=pos_days, drops=int(DR[i]), fcross1=int(X1[i].sum()), fcross2=int(X2[i].sum()),
                         _net=net, _cnt=C[i]))
        if mean > 0 and pos_days >= 5: st[2] += 1
cand = [r for r in rows if r['net_mean'] > 0 and r['pos_days'] >= 5]
for r in cand:
    nb = r['_net']; cb = r['_cnt']; cnt = 0; tot = 0
    for _ in range(max(1, B // 20000)):
        idx = rng.integers(0, 168, size=(20000, 168))
        den = cb[idx].sum(1); num = nb[idx].sum(1)
        m = np.where(den > 0, num / np.where(den > 0, den, 1), 0.0)
        cnt += int(np.sum(m <= 0)); tot += 20000
    r['p'] = (cnt + 1) / (tot + 1)
Nsel = len(rows)
ps = sorted(cand, key=lambda r: r['p'])
good = []
for j, r in enumerate(ps):
    if r['p'] < 0.05 / (Nsel - j): r['holm'] = 1; good.append(r)
    else: break
import csv
cols = ['coin', 'pair', 'mode', 'anchor', 'scen', 'k', 'delay_s', 'timeout_s', 'circle', 'n_trades', 'n_ep', 'gross_mean', 'net_mean', 'pos_days', 'drops', 'fcross1', 'fcross2', 'p', 'holm']
with open(out, 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction='ignore'); w.writeheader()
    for r in sorted(cand, key=lambda r: r['p']): w.writerow(r)
print('клеток всего', ncell_all, '; прошли эфф.N>=30:', Nsel, '; net>0 и >=5/7 дней:', len(cand), '; Холм-годных:', len(good), '; B на клетку', B)
for key, v in sorted(sel_stat.items()): print(key, v)
if rows:
    bm = sorted(rows, key=lambda r: -r['net_mean'])[:5]
    print('топ-5 по net_mean среди эфф.N>=30:'); [print({c: r[c] for c in cols if c in r}) for r in bm]
    print('медиана net_mean по клеткам эфф.N>=30:', float(np.median([r['net_mean'] for r in rows])), '; gross_mean:', float(np.median([r['gross_mean'] for r in rows])))
