#!/usr/bin/env python3
"""TK-345: независимый пересчёт n сигналов клетки по кэшу касаний (без кода bounce-grid).

Использование: tk345-n-recount.py <touches-dir(D20/сутки)> <instruments.csv> <forms.csv> <side> <h3_usd> <форма-для-сверки>
n_ожид(symbol) = число касаний side с size_at_touch * price_tick * tick_size * qty_step >= h3_usd.
Сверка с n_signals и n_signals+n_skipped формы в forms.csv. Печатает расхождения и итог.
"""
import csv, os, sys
tdir, inst, forms, side, usd, form = sys.argv[1:7]
usd = float(usd)
ins = {r["symbol"]: (float(r["tick_size"]), float(r["qty_step"])) for r in csv.DictReader(open(inst))}
exp, tot = {}, {}
for sym, (tick, step) in ins.items():
    p = os.path.join(tdir, f"touches-{sym}.csv")
    if not os.path.exists(p):
        continue
    n = a = 0
    for r in csv.DictReader(open(p)):
        a += 1
        if r["side"] != side:
            continue
        if int(r["size_at_touch"]) * int(r["price_tick"]) * tick * step >= usd:
            n += 1
    exp[sym], tot[sym] = n, a
got = {}
for r in csv.DictReader(l for l in open(forms) if not l.startswith("#")):
    if r.get("form") != form:
        continue
    got[r["symbol"]] = (int(r["n_signals"]), int(r["n_skipped"]))
bad = 0
for sym, (s, k) in sorted(got.items()):
    e = exp.get(sym)
    if e != s or s + k != tot.get(sym):
        bad += 1
        print(f"DIFF {sym}: n_signals={s} ожид={e}; signals+skipped={s+k} касаний_всего={tot.get(sym)}")
print(f"форма {form}: символов {len(got)}, расхождений {bad}, sum n_signals={sum(v[0] for v in got.values())}, ожид={sum(exp.get(s,0) for s in got)}")
