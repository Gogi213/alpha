#!/usr/bin/env python3
"""TK-464 гейт парного режима ядра (--pair): суммы (c, d, b, k, d2) по группам (ячейка, ev, dir, сутки tt0 события)
против tk461_d2a.one на построчных выводах того же ядра (scan события, ctrl контроли).
python tk464_pair_gate.py <scan.csv[.gz]> <ctrl.csv[.gz]> <pair.csv> -> 'IDENTICAL_PAIR групп N' (rc 0) или 'DIFF ...' (rc 1).
c — точно; d, b, k, d2 — rtol 1e-9 (порядок суммирования другой)."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/data/tk0461")
import tk461_d2a as D  # noqa: E402

KEY = D.KEY


def main(fs, fc, fp):
    r = D.one((fs, fc))
    p = pd.read_csv(fp)
    if r is None:
        print("IDENTICAL_PAIR групп", len(p)) if len(p) == 0 else print("DIFF эталон пуст, ядро", len(p))
        return 0 if len(p) == 0 else 1
    g = r[0].copy()
    for k in KEY:
        g[k] = g[k].astype(str)
    p = p.assign(tf=p.tf.astype(str), par=p.par.astype(str)).rename(columns={"c": "c"})
    g["par"] = g["par"].astype(float).astype(str)
    p["par"] = p["par"].astype(float).astype(str)
    g["tf"] = g["tf"].astype(np.int64).astype(str)
    p["tf"] = p["tf"].astype(np.int64).astype(str)
    key = KEY + ["day"]
    g = g.set_index(key).sort_index()
    p = p.set_index(key).sort_index()
    bad = []
    if len(g) != len(p):
        bad.append(f"групп {len(p)} != {len(g)}")
    if not g.index.equals(p.index):
        bad.append("ключи не совпали")
    else:
        if not (g.c.values == p.c.values).all():
            bad.append("счётчик c")
        for c in ["d", "b", "k", "d2"]:
            if not np.allclose(p[c].values, g[c].values, rtol=1e-9, atol=1e-6):
                bad.append("сумма " + c)
    if bad:
        print("DIFF", "; ".join(bad))
        return 1
    print("IDENTICAL_PAIR групп", len(g), "пар", int(g.c.sum()))
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:4]))
