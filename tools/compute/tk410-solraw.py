#!/usr/bin/env python3
"""TK-410 В1–В5, Solana: готовые событийные свопы tk407 (mid-sol3-med/COIN.csv.gz: ts,slot,txidx,price в единицах квоты, список list-sol3-med.txt, пометка SOL = квота WSOL)
-> raw-csv формата tk410-map2 (ts,block,logidx,kind,mid_raw,liq0,liq1,tick), цена в USD (W11): для WSOL × середина Gate SOL (gev2all/SOL, последняя ≤ ts).
Середины пула у Solana нет — цена исполнения свопа (оговорка в отчёт). Запуск (calc, cwd /data/tk0393/tk410): tk410-solraw.py; пишет sol-raw/COIN.csv и list-sol.txt ('COIN FILE')."""
import glob, gzip, os, numpy as np
S = '/data/tk0393/tk407/'; G = '/data/tk0393/tk401/gev2all'
parts = [np.load(f) for f in sorted(glob.glob(f'{G}/SOL/??????????.npy'))]
a = np.concatenate(parts); a = a[np.argsort(a[:, 0], kind='stable')]
ok = np.isfinite(a[:, 1]) & np.isfinite(a[:, 2]) & (a[:, 1] < a[:, 2]); a = a[ok]
st, sm = a[:, 0] / 1000.0, (a[:, 1] + a[:, 2]) / 2
os.makedirs('sol-raw', exist_ok=True); out = []
for ln in open(S + 'list-sol3-med.txt'):
    p = ln.split(); c = p[0]; sol = len(p) > 1
    if not os.path.isdir(f'{G}/{c}'): print('nogev', c); continue
    r = np.loadtxt(gzip.open(f'{S}mid-sol3-med/{c}.csv.gz', 'rt'), delimiter=',', ndmin=2)
    if len(r) < 200: continue
    ts, px = r[:, 0], r[:, 3].copy()
    if sol:
        i = np.searchsorted(st, ts, 'right') - 1; m = (i >= 0) & (ts - st[np.clip(i, 0, None)] < 600)
        r, ts, px = r[m], ts[m], px[m] * sm[i[m]]
    with open(f'sol-raw/{c}.csv', 'w') as f:
        f.write('ts,block,logidx,kind,mid_raw,liq0,liq1,tick\n')
        for t, s, x, p_ in zip(ts, r[:, 1], r[:, 2], px): f.write(f'{int(t)},{int(s)},{int(x)},sol,{p_:.12g},,,\n')
    out.append(f'{c} sol-raw/{c}.csv')
open('list-sol.txt', 'w').write('\n'.join(out) + '\n'); print(len(out), 'монет')
