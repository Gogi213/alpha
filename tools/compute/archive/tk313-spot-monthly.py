#!/usr/bin/env python3
"""TK-313: помесячная таблица спот vs фьюч (n, Σ$ (а), SR сут.) на общих сутках и общих монетах; только чтение готовых rounds.csv.
Загрузчики — из tk313-spot-stats.py (его префикс до bootstrap). Выход: JSON в argv[1]."""
import sys, json
src = open("/data/tk0313/tk313-spot-stats.py").read()
head = src.split("spot, sdays = load_spot()")[0]
OUTP = sys.argv[1]
sys.argv = [sys.argv[0], "/dev/null"]
exec(head)
spot, sdays = load_spot()
fut, fdays = load_fut()
common = [d for d in sdays if d in fdays]
months = sorted({d[:7] for d in common})
fs = {x[0] for k in fut for d in fut[k] for x in fut[k][d]}
ss = {x[0] for c in spot for d in spot[c] for x in spot[c][d]}
cs = fs & ss
out = {"common_days": len(common), "common_syms": len(cs), "cells": {}}
for c in ["s0", "g-s50-u1", "g-s50-u2", "g-s50-u3", "g-s25-u3"]:
    k = FUTMAP[c]
    rows = {}
    for m in months:
        ds = [d for d in common if d[:7] == m]
        r = {}
        for side, src_d, key in (("spot", spot.get(c, {}), c), ("fut", fut.get(k, {}), k)):
            daily = [sum(x[1] for x in src_d.get(d, []) if x[0] in cs) for d in ds]
            n = sum(1 for d in ds for x in src_d.get(d, []) if x[0] in cs)
            r[side] = {"n": n, "sum_a": round(sum(daily), 1), "sr_day": round(sr(daily), 3)}
        rows[m] = r
    out["cells"][c] = {"fut_key": k, "months": rows}
json.dump(out, open(OUTP, "w"), ensure_ascii=False, indent=1)
print("ok", OUTP)
