"""TK-268: Г-147 (R-20261017-008, 1 хеш × 9 мес.) в Летопись на calc дописью (без дублей): results, cells, run_cells, runs, metrics. Бэкап /data/launch/prev-tk268g147/registry.
Запуск на calc: python3 tk268-deploy-g147.py <каталог add-*.jsonl>"""
import json, os, shutil, sys
A = sys.argv[1]; R = "/data/launch/registry"; B = "/data/launch/prev-tk268g147/registry"
os.makedirs(B, exist_ok=True)
NAMES = ("results", "cells", "run_cells", "runs", "metrics")
for n in NAMES:
    dst = f"{B}/{n}.jsonl"
    if not os.path.exists(dst):
        shutil.copy2(f"{R}/{n}.jsonl", dst)
def lines(p):
    return [l.rstrip("\r\n") for l in open(p, encoding="utf-8", newline="") if l.strip()]
keys = {"results": lambda d: (d["run_id"], d["cell_id"], d["month"]), "cells": lambda d: d["id"], "run_cells": lambda d: (d["run_id"], d["cell_id"]), "runs": lambda d: d["id"], "metrics": lambda d: d["hash"]}
for n in NAMES:
    have = {keys[n](json.loads(l)) for l in lines(f"{R}/{n}.jsonl")}
    new = [l for l in lines(f"{A}/add-{n}.jsonl") if keys[n](json.loads(l)) not in have]
    with open(f"{R}/{n}.jsonl", "a", encoding="utf-8", newline="") as f:
        f.writelines(l + "\n" for l in new)
    print(n, "+", len(new))
