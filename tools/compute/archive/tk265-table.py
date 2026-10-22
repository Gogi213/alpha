# TK-265: таблица «есть / считаем» на calc (только чтение): ssh calc python3 -I - < tools/compute/archive/tk265-table.py
import sys, json, collections
sys.path.insert(0, "/opt/alpha-launch/tools/launch")
import launch
cfg = launch.load_config()
rows = launch.plan_rows(cfg["protocol"], cfg["fraction"])
hyps = sorted({r["hyp"] for r in rows})
months = cfg["months"]
p = launch.plan(hyps, months)
out = {k: v for k, v in p.items() if k != "cells"}
print(json.dumps(out, ensure_ascii=False))
# по месяцам
per = collections.OrderedDict((m, [0, 0, 0, 0]) for m in months)   # have, new, off, queued
byh = collections.defaultdict(lambda: [0, 0, 0, 0])
offr = collections.defaultdict(lambda: collections.Counter())
for c in p["cells"]:
    for m in c["have_months"]: per[m][0] += 1; byh[c["hyp"]][0] += 1
    for m in c["new_months"]: per[m][1] += 1; byh[c["hyp"]][1] += 1
    for m in c["off_months"]: per[m][2] += 1; byh[c["hyp"]][2] += 1; offr[c["hyp"]][c["route"]] += 1
    for m in c["queued_months"]: per[m][3] += 1; byh[c["hyp"]][3] += 1
print("PER_MONTH have new off queued")
for m, v in per.items(): print(m, *v)
print("PER_HYP have new off queued | off routes")
for h in sorted(byh): print(h, *byh[h], dict(offr[h]) if h in offr else "")
