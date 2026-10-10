#!/usr/bin/env python3
"""TK-071 v2 гейт (а): строки util.log с queued>0 после старта SCHED_PACK=fact; выходит, когда набрано N строк (по умолчанию
120 ≈ 2 ч) — пишет отчёт. Среднее host — ВЗВЕШЕННОЕ по Δt до предыдущей строки (host = доля «с прошлой строки»; Судья 09.10:
невзвешенное завышало, окна stand растягивали строку), отдельно: вне окон (measure=0), окна (measure=1: доля времени и host),
по часам, строки с legacy>0. Аргументы: since N out."""
import sys, time, collections
from datetime import datetime
since, need, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
def ts(t):
    return datetime.fromisoformat(t).timestamp()
def rows():
    r, prev = [], None
    for l in open("/data/sched/util.log"):
        t, rest = l.split(" ", 1)
        d = dict(kv.split("=") for kv in rest.split())
        dt = ts(t) - prev if prev is not None else 0
        prev = ts(t)
        if t < since or dt <= 0 or int(d["queued"]) == 0:
            continue
        r.append((t, float(d["host"]), int(d["legacy"]), int(d["measure"]), dt))
    return r
while True:
    r = rows()
    if len(r) >= need:
        break
    time.sleep(60)
def wm(x):
    w = sum(i[4] for i in x)
    return (sum(i[1] * i[4] for i in x) / w if w else 0), w
by = collections.defaultdict(list)
for i in r:
    by[i[0][:13]].append(i)
with open(out, "w") as f:
    m, w = wm(r)
    f.write(f"строк {len(r)} (queued>0) с {since}, {w:.0f} с\nсреднее host по времени {m:.3f}\n")
    for tag, sel in (("вне окон (measure=0)", [i for i in r if not i[3]]), ("окна (measure=1)", [i for i in r if i[3]])):
        m, w2 = wm(sel)
        f.write(f"{tag}: строк {len(sel)}, доля времени {w2 / w:.3f}, host {m:.3f}\n")
    for h, v in sorted(by.items()):
        m, w2 = wm(v)
        f.write(f"{h} n={len(v)} по времени={m:.3f} (вне окон {wm([i for i in v if not i[3]])[0]:.3f})\n")
    lg = [i for i in r if i[2] > 0]
    f.write(f"строк с legacy>0: {len(lg)}, host по времени {wm(lg)[0]:.3f}\n")
