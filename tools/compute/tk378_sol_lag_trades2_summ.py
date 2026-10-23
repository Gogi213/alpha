#!/usr/bin/env python3
"""TK-378 v2: свод episodes.csv -> блочный бутстрап по суткам (монета×сутки), net после издержек.
Запуск: tk378_sol_lag_trades2_summ.py episodes.csv summ.json OUT.json"""
import sys, csv, json, random, statistics, collections
GATE_RT = 15.0  # тейкер Gate 0,075 % x2 (contracts, TK-377)
SOL_USD = 190.0  # только для перевода priority fee в $; порядок, на 1000 $ ноги влияет < 1 бп
eps = list(csv.DictReader(open(sys.argv[1]))); summ = {s['coin']: s for s in json.load(open(sys.argv[2]))}
def fee_bp(c, notional=1000.0):
    f = summ[c].get('fee_lamports_med')
    return f * 1e-9 * SOL_USD / notional * 1e4 if f else 0.0
rnd = random.Random(378)
def boot(vals, B=4000):  # vals: средние по монето-суткам; бутстрап по суткам
    if len(vals) < 2: return None
    ms = sorted(statistics.mean(rnd.choice(vals) for _ in vals) for _ in range(B))
    return round(statistics.mean(vals), 1), round(ms[int(.025 * B)], 1), round(ms[int(.975 * B)], 1)
out = {}
for X in (50, 100, 200, 500):
    for c in sorted({e['coin'] for e in eps}) + ['ALL4']:
        cs = ['CLAUDIA', 'FONE', 'HOOKED', 'SI'] if c == 'ALL4' else [c]
        sel = [e for e in eps if e['coin'] in cs and int(e['X']) == X]
        if not sel: continue
        byd = collections.defaultdict(list)
        for e in sel: byd[(e['coin'], e['day'])].append(e)
        ex, nt, nt2, n_ctl = [], [], [], 0
        for (cc, d), es in byd.items():
            wc = [float(e['pnl']) - float(e['ctl']) for e in es if e['ctl'] != '']
            n_ctl += len(wc)
            if wc: ex.append(statistics.mean(wc))
            nt.append(statistics.mean(float(e['pnl']) for e in es) - GATE_RT)
            nt2.append(nt[-1] - fee_bp(cc))
        out[f'{c}|{X}'] = dict(coin=c, X=X, episodes=len(sel), with_ctl=n_ctl, units=len(byd), med_pnl=round(statistics.median(float(e['pnl']) for e in sel), 1),
                               excess=boot(ex), net_gate=boot(nt), net_gate_dexfee=boot(nt2), fee_bp_1000usd=round(fee_bp(cs[0]), 2) if len(cs) == 1 else None,
                               days_pos_excess=sum(1 for v in ex if v > 0), days=len(ex))
json.dump(out, open(sys.argv[3], 'w'), ensure_ascii=False, indent=1)
for k, v in out.items(): print(k, v['episodes'], v['with_ctl'], v['units'], 'ex', v['excess'], 'net', v['net_gate'], v['net_gate_dexfee'])
