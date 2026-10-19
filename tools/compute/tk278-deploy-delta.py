"""TK-278: F2 Г-117 (18 хешей) + R1 Г-07/107/27/32/89 (24 хеша) в панель на calc. Бэкап /data/launch/prev-tk278b; дописью (без дублей): results, cells,
run_cells, runs, plan_cells (P-14 F2), metrics (по hash); routes.json — маршруты 42 хешей. Запуск на calc: tk278-deploy-delta.py <каталог add-*>"""
import json, os, shutil, sys
A = sys.argv[1]; L = "/opt/alpha-launch/tools/launch"; R = "/data/launch/registry"; B = "/data/launch/prev-tk278b"
os.makedirs(B + "/registry", exist_ok=True)
for src in [R + f"/{n}.jsonl" for n in ("results", "cells", "run_cells", "runs", "plan_cells", "metrics")] + [L + "/routes.json"]:
    dst = B + ("/registry/" if src.startswith(R) else "/") + os.path.basename(src)
    if not os.path.exists(dst):
        shutil.copy2(src, dst)


def lines(p):
    return [l.rstrip("\r\n") for l in open(p, encoding="utf-8", newline="") if l.strip()]


keys = {"results": lambda d: (d["run_id"], d["cell_id"], d["month"]), "cells": lambda d: d["id"], "run_cells": lambda d: (d["run_id"], d["cell_id"]),
        "runs": lambda d: d["id"], "plan_cells": lambda d: (d["protocol"], d["fraction"], d["cell_hash"]), "metrics": lambda d: d["hash"]}
for n, k in keys.items():
    have = {k(json.loads(l)) for l in lines(f"{R}/{n}.jsonl")}
    new = [l for l in lines(f"{A}/add-{n}.jsonl") if k(json.loads(l)) not in have]
    with open(f"{R}/{n}.jsonl", "a", encoding="utf-8", newline="") as f:
        f.writelines(l + "\n" for l in new)
    print(n, "+", len(new))
rt = json.load(open(L + "/routes.json", encoding="utf-8")); add = json.load(open(A + "/add-routes.json", encoding="utf-8"))
for sec in ("routes", "lines", "monthly", "sets"):
    n0 = len(rt[sec]); rt[sec].update({h: v for h, v in add[sec].items() if h not in rt[sec]}); print("routes." + sec, "+", len(rt[sec]) - n0)
open(L + "/routes.json.new", "w", encoding="utf-8", newline="\n").write(json.dumps(rt, ensure_ascii=False))
os.replace(L + "/routes.json.new", L + "/routes.json")
