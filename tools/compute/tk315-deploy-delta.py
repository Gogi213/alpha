"""TK-315: F2 П-17 (383 хеша) в панель на calc. Бэкап /data/launch/prev-tk315; дописью (без дублей): results, cells, run_cells, runs,
plan_cells (P-17 F2), metrics (по hash), hypotheses; routes.json — маршруты + sets состояний; config.protocols P-17 -> [1,2].
Запуск на calc: tk315-deploy-delta.py <каталог add-*>"""
import json, os, shutil, sys
A = sys.argv[1]; L = "/opt/alpha-launch/tools/launch"; R = "/data/launch/registry"; B = "/data/launch/prev-tk315"
os.makedirs(B + "/registry", exist_ok=True)
for src in [R + f"/{n}.jsonl" for n in ("results", "cells", "run_cells", "runs", "plan_cells", "metrics", "hypotheses")] + [L + "/routes.json", L + "/config.json"]:
    dst = B + ("/registry/" if src.startswith(R) else "/") + os.path.basename(src)
    if not os.path.exists(dst):
        shutil.copy2(src, dst)
def lines(p):
    return [l.rstrip("\r\n") for l in open(p, encoding="utf-8", newline="") if l.strip()]
keys = {"results": lambda d: (d["run_id"], d["cell_id"], d["month"]), "cells": lambda d: d["id"], "run_cells": lambda d: (d["run_id"], d["cell_id"]),
        "runs": lambda d: d["id"], "plan_cells": lambda d: (d["protocol"], d["fraction"], d["cell_hash"]), "metrics": lambda d: d["hash"], "hypotheses": lambda d: d["id"]}
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
c = json.load(open(L + "/config.json", encoding="utf-8"))
for p in c["protocols"]:
    if p[0] == "P-17" and p[1] != [1, 2]:
        p[1] = [1, 2]
        open(L + "/config.json.new", "w", encoding="utf-8", newline="\n").write(json.dumps(c, ensure_ascii=False, indent=2) + "\n")
        os.replace(L + "/config.json.new", L + "/config.json")
print("config.protocols", c["protocols"])
