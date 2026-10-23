#!/usr/bin/env python3
"""TK-377 шаг 3: сводка episodes.csv (tk377-lag2.py): класс × X × чистота, сверх контроля по ходу Gate, net; кластер = монето-сутки. Запуск: tk377-lag2-summary.py EPISODES.csv OUT.csv"""
import sys, csv, math, collections, statistics
rows = list(csv.DictReader(open(sys.argv[1], encoding='utf8'))); FEE = 15.0; MKR = -2.0
def f(x): return float(x) if x != '' else None
out = []
for cls in ('с тяжеловесами', 'без тяжеловесов', 'все'):
    for X in (50, 100, 200, 500):
        for clean in ('все', 'чистые (обе цены <=60 с)', 'DEX устарел (>60 с)'):
            rs = [r for r in rows if (cls == 'все' or r['cls'] == cls or (cls == 'без тяжеловесов' and r['cls'] == 'без')) and int(r['X']) == X]
            if clean.startswith('чист'): rs = [r for r in rs if int(r['gate_age']) <= 60 and 0 <= int(r['dex_age']) <= 60]
            elif clean.startswith('DEX'): rs = [r for r in rs if int(r['dex_age']) > 60]
            if not rs: continue
            cl = collections.defaultdict(list)
            for r in rs: cl[(r['coin'], r['day'])].append(r)
            ex = []; pn = []; cv = 0; g2d = 0; d2g = 0
            for r in rs:
                p = float(r['pnl3600']); c = f(r['ctl_gm']); pn.append(p)
                if c is not None: ex.append((r['coin'], r['day'], p - c))
                if r['conv_s'] != '': cv += 1
                if float(r['gate_part']) > float(r['dex_part']): g2d += 1
                else: d2g += 1
            # кластерное среднее по монето-суткам
            cm = collections.defaultdict(list)
            for c, d, e in ex: cm[(c, d)].append(e)
            cmv = [sum(v) / len(v) for v in cm.values()]
            mean_ex = sum(e for _, _, e in ex) / len(ex) if ex else float('nan')
            se = statistics.stdev(cmv) / math.sqrt(len(cmv)) if len(cmv) > 1 else float('nan')
            out.append(dict(cls=cls, X_pct=X / 100, sel=clean, episodes=len(rs), coins=len({r['coin'] for r in rs}), coin_days=len(cl), conv_share=round(cv / len(rs), 3),
                            gate_to_dex=g2d, dex_to_gate=d2g, mae_med=round(statistics.median(float(r['mae']) for r in rs), 1), pnl3600=round(sum(pn) / len(pn), 1),
                            excess_vs_ctl=round(mean_ex, 1), excess_se_cluster=round(se, 1), n_ctl=len(ex), net_taker=round(sum(pn) / len(pn) - FEE, 1),
                            net_maker_upper=round(sum(pn) / len(pn) - MKR * 0 + 2, 1)))
w = csv.DictWriter(open(sys.argv[2], 'w', newline='', encoding='utf8'), list(out[0])); w.writeheader(); w.writerows(out)
sys.stdout.reconfigure(encoding='utf8')
for o in out: print(o)
