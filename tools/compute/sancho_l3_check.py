#!/usr/bin/env python3
"""TK-455 / P-TK455 §8 п.1: сверка числа и моментов сигналов слоя 3 с событиями слоя 2 (воронка TK-451), по (роль, класс, сторона).
python tools/compute/sancho_l3_check.py <csv.gz минуты> <tf_min> <k> <l2 csv.gz> <out.json>
Класс по слою 2: A1 brk&u_a0>=0; A4 = A1 & depth>0.5; A5 !brk&u_a0>=0; A6 !brk&imb&u_a2>=0; A7 !brk&bos&u_a1>=0; A8 = A6&bos.
Сравнивается мультимножество баров касания tj (слой 2: t). Расхождение -> код 1."""
import json
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sancho_l3_sig import sig_table  # noqa: E402


def l2_classes(d):
    nb = d.brk == 0
    return {"A1": (d.brk == 1) & (d.u_a0 >= 0), "A4": (d.brk == 1) & (d.u_a0 >= 0) & (d.depth > 0.5),
            "A5": nb & (d.u_a0 >= 0), "A6": nb & (d.imb == 1) & (d.u_a2 >= 0),
            "A7": nb & (d.bos == 1) & (d.u_a1 >= 0), "A8": nb & (d.imb == 1) & (d.bos == 1) & (d.u_a2 >= 0)}


def main():
    path, tf, k, l2p, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5]
    s = sig_table(path, tf, k)
    d = pd.read_csv(l2p)
    res = {"n_sig": int(len(s)), "n_l2": int(len(d)), "bad": []}
    for ev, m in l2_classes(d).items():
        for role in ("real", "C1", "C2"):
            for dr in ("H", "L"):
                a = np.sort(d.t[m & (d.role == role) & (d.dir == dr)].values)
                b = np.sort(s.tj[(s.ev == ev) & (s.role == role) & (s.dir == dr)].values) if len(s) else np.array([])
                if len(a) != len(b) or not np.array_equal(a, b):
                    res["bad"].append([ev, role, dr, int(len(a)), int(len(b))])
    json.dump(res, open(out, "w"))
    print(os.path.basename(path), tf, k, "OK" if not res["bad"] else f"BAD {res['bad'][:3]}")
    sys.exit(1 if res["bad"] else 0)


if __name__ == "__main__":
    main()
