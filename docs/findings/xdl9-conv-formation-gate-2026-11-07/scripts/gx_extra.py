#!/usr/bin/env python3
"""TK-493, задача C (не код репозитория): описательные добавки к выжимке набора: c_полн на Pi_train, c_обуч на Pi_full, расстояние согласия по осям, пересечение списков пар.
Только чтение готовых сеток формирования. Окно не читается.
  python gx_extra.py --out DIR --name NAME --dest DEST
"""
import argparse
import json
import math
import os
import sys

import numpy as np

CODE = os.path.expanduser("~/xdl9/c2g/repo/tools/compute/statarb/xdl9/conv")
sys.path.insert(0, CODE)
import conv_agg as AG       # noqa: E402
import gx_extract as X      # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--dest", required=True)
    a = ap.parse_args()
    res = {"name": a.name}
    for cls in ("crypto",):
        sel = json.load(open(os.path.join(a.out, "k7", "selection_%s.json" % cls), encoding="utf-8"))
        gf = AG.Grid(os.path.join(a.out, "calib", "grid_full"), cls)
        gt = AG.Grid(os.path.join(a.out, "train", "grid_train"), cls)
        cf, ct = sel.get("c_full"), sel.get("c_train")
        allf, allt = np.arange(len(gf.pids)), np.arange(len(gt.pids))
        r = {"c_full": cf, "c_train": ct}
        if cf:
            r["c_full_on_train"] = X.per_rows(gt, gt.cindex[tuple(cf)], allt)
        if ct:
            r["c_train_on_train"] = X.per_rows(gt, gt.cindex[tuple(ct)], allt)
            r["c_train_on_full"] = X.per_rows(gf, gf.cindex[tuple(ct)], allf)
        if cf and ct:
            r["step_distance"] = AG.step_distance(tuple(cf), tuple(ct))
        r["train_pairs"] = len(gt.pids)
        r["train_days"] = gt.d1 - gt.first_sig + 1
        # соседи c_полн (плато): среднее y_d на Pi_full
        if cf:
            nb = []
            for ci in AG.neighbors(gf, gf.cindex[tuple(cf)]):
                ser = AG.y_series(gf, ci, allf)["primary"]
                nb.append(dict(combo=list(gf.combos[ci]), mean_yd=(sum(ser) / len(ser)) if ser else None, trades=AG.n_trades(gf, ci, allf)))
            r["neighbors_full"] = nb
        res[cls] = r
    p = os.path.join(a.dest, "extra_%s.json" % a.name)

    def fix(o):
        if isinstance(o, (np.floating, float)):
            return None if not math.isfinite(float(o)) else float(o)
        if isinstance(o, np.integer):
            return int(o)
        return str(o)
    os.makedirs(a.dest, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, sort_keys=True, default=fix)
    print("extra:", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
