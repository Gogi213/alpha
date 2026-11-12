#!/usr/bin/env python3
"""TK-394 шаг 1 (возврат): свод spread3/*.json -> таблицы по парам/базису. Запуск: tk394-agg3.py DIR [OUT.csv]"""
import sys, json, glob
import numpy as np
D = sys.argv[1]
rows = []
for f in glob.glob(f'{D}/*.json'):
    if not f.endswith('DONE.json'): rows += json.load(open(f))
def med(x): x = [v for v in x if v is not None and np.isfinite(v)]; return float(np.median(x)) if x else float('nan')
out = []
for pair in ('FF', 'FS'):
    for base in ('1h', '24h'):
        R = [r for r in rows if r['pair'] == pair and r['base'] == base]
        coins = sorted({r['coin'] for r in R})
        pc = []
        for c in coins:
            rc = [r for r in R if r['coin'] == c]
            nv = sum(r['n_valid'] for r in rc)
            nr = sum(r['n_raw'] for r in rc); nf = sum(r['n_fresh'] for r in rc)
            er = sum(r['ep_raw'] for r in rc); ef = sum(r['ep_fresh'] for r in rc)
            gn = sum(r.get('g_n', 0) for r in rc)
            gm = sum(r.get('g_mean', 0) * r.get('g_n', 0) for r in rc) / gn if gn else float('nan')
            nm = sum(r.get('net_mean_entry', 0) * r.get('g_n', 0) for r in rc) / gn if gn else float('nan')
            nd = sum(r.get('net_mean_disc', 0) * r.get('g_n', 0) for r in rc) / gn if gn else float('nan')
            sh = sum(r.get('share_g_gt_entry', 0) * r.get('g_n', 0) for r in rc) / gn if gn else float('nan')
            days_ep = sum(1 for r in rc if r['ep_fresh'] > 0)
            pc.append(dict(coin=c, n_valid=nv, n_raw=nr, n_fresh=nf, ep_raw=er, ep_fresh=ef, g_n=gn, g_mean=gm, net_entry=nm, net_disc=nd,
                           share_gt_entry=sh, days_with_ep=days_ep,
                           cA=med([r.get('close_A_gate_bp') for r in rc]), cB=med([r.get('close_B_bybit_bp') for r in rc])))
        pos = [p for p in pc if p['n_raw'] > 0]
        fr = [p for p in pc if p['n_fresh'] > 0]
        print(f'{pair} {base}: монет {len(pc)}; с сигналом(raw) {len(pos)}; со свежим сигналом {len(fr)}')
        print(f'  доля сек. raw/valid (медиана по монетам): {med([p["n_raw"]/p["n_valid"] for p in pc if p["n_valid"]])*100:.3f} %; fresh/valid: {med([p["n_fresh"]/p["n_valid"] for p in pc if p["n_valid"]])*100:.3f} %')
        print(f'  отсеяно свежестью (сумма fresh/raw): {sum(p["n_fresh"] for p in pc)/max(1,sum(p["n_raw"] for p in pc))*100:.1f} %')
        print(f'  эпизодов raw/сут (медиана): {med([p["ep_raw"]/7 for p in pc]):.1f}; fresh/сут: {med([p["ep_fresh"]/7 for p in fr]):.1f}; всего fresh {sum(p["ep_fresh"] for p in pc)}')
        print(f'  по свежим: g_mean медиана {med([p["g_mean"] for p in fr]):.2f} бп; net(вход) медиана {med([p["net_entry"] for p in fr]):.2f}; net(скидка) {med([p["net_disc"] for p in fr]):.2f}; доля g>вход {med([p["share_gt_entry"] for p in fr])*100:.1f} %')
        print(f'  монет net(вход)>0: {sum(1 for p in fr if p["net_entry"]>0)} из {len(fr)}; net(скидка)>0: {sum(1 for p in fr if p["net_disc"]>0)}; с ≥10 свежими эпизодами и net>0: {sum(1 for p in fr if p["ep_fresh"]>=10 and p["net_entry"]>0)}')
        print(f'  кто к кому (бп закрытия за 5 с, медиана): Gate {med([p["cA"] for p in fr]):.2f}, Bybit {med([p["cB"] for p in fr]):.2f}')
        for p in pc: out.append(dict(pair=pair, base=base, **p))
import csv
if len(sys.argv) > 2:
    with open(sys.argv[2], 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0])); w.writeheader(); w.writerows(out)
