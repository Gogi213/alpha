#!/usr/bin/env python3
"""TK-433: почему у события нет контроля ±30 сут (вопрос владельца 28.10). Для событий θ60 без контроля — разбор 708 кандидатов-окон
по причине отказа; «если бы» — сколько кандидатов дал бы август (свечи есть) / октябрь (свечей нет — не считается)."""
import sys, os, json, csv, importlib.util
from collections import defaultdict, Counter
sp = importlib.util.spec_from_file_location("f", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tk433-features.py"))
F = importlib.util.module_from_spec(sp)
sp.loader.exec_module(F)
MIN = F.MIN
l60, l240, root, aug, diagf, outf = sys.argv[1:7]
lo, hi = F.month_bounds("2026-09")
roots = (aug, root)
legs = {t: list(csv.DictReader(open(fn, encoding="utf-8"))) for t, fn in (("60", l60), ("240", l240))}
busy = defaultdict(list)
for rs in legs.values():
    for r in rs:
        busy[(r["биржа"], r["рынок"], r["инструмент"])].append((int(r["t_start_ms"]) // MIN, int(r["t_peak_ms"]) // MIN))
diag = json.load(open(diagf, encoding="utf-8"))
nc = set(diag["без_контролей"])
short = set(diag["короткая_история"])
res = []
n = 0
for r in legs["60"]:
    if r["исключено"] == "" and r.get("представитель") == "1":
        n += 1
        eid = "E60-%d" % n
        if eid not in nc:
            continue
        ex, mk, sym = r["биржа"], r["рынок"], r["инструмент"]
        bz = busy[(ex, mk, sym)]
        d = F.load(roots, "%s-%s" % (ex, mk), sym)
        a, e = int(r["t_start_ms"]) // MIN, int(r["t_peak_ms"]) // MIN
        dur = e - a
        frac = sum(1 for m in range(a, e + 1) if m in d) / (dur + 1)
        hs = a - a % 60
        c = Counter()
        for dd in range(-30, 31):
            if dd == 0:
                continue
            for k in range(0, 60, 5):
                s = hs + dd * 1440 + k
                ps, es = s - F.PRE, s + dur
                have = s in d and es in d and sum(1 for m in range(s, es + 1) if m in d) >= frac * (dur + 1)
                isb = any(not (es < b0 or ps > b1) for b0, b1 in bz)
                if ps < lo:
                    c["до_месяца"] += 1
                    c["август_годен"] += (not isb) and have
                elif es >= hi:
                    c["после_месяца"] += 1
                elif isb:
                    c["занято_ногой"] += 1
                elif not have:
                    c["нет_свечей"] += 1
                else:
                    c["годен"] += 1
        res.append(dict(id=eid, инструмент=sym, рынок=mk, знак=r["знак"], длит_мин=dur, короткая_история=eid in short, **dict(c)))
json.dump(res, open(outf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(res))
