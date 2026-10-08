#!/usr/bin/env python3
"""TK-071 v2 гейт (а): строки util.log с queued>0 и measure=0 после старта SCHED_PACK=fact; выходит, когда набрано N строк
(по умолчанию 120 ≈ 2 ч) — пишет отчёт: среднее host по часам и в целом, минимум, строки с legacy>0. Аргументы: since N out."""
import sys, time, re, collections
since, need, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
def rows():
    r = []
    for l in open("/data/sched/util.log"):
        t, rest = l.split(" ", 1)
        if t < since:
            continue
        d = dict(kv.split("=") for kv in rest.split())
        if int(d["queued"]) > 0 and d["measure"] == "0":
            r.append((t, float(d["host"]), int(d["legacy"]), int(d["prod_run"])))
    return r
while True:
    r = rows()
    if len(r) >= need:
        break
    time.sleep(60)
by = collections.defaultdict(list)
for t, h, lg, pr in r:
    by[t[:13]].append(h)
with open(out, "w") as f:
    f.write(f"строк {len(r)} (queued>0, measure=0) с {since}\nсреднее host {sum(x[1] for x in r)/len(r):.3f}, минимум {min(x[1] for x in r):.3f}\n")
    for h, v in sorted(by.items()):
        f.write(f"{h} n={len(v)} mean={sum(v)/len(v):.3f}\n")
    lg = [x for x in r if x[2] > 0]
    f.write(f"строк с legacy>0: {len(lg)}, среднее host там {sum(x[1] for x in lg)/len(lg) if lg else 0:.3f}\n")
