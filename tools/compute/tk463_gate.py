#!/usr/bin/env python3
"""TK-463: подготовка и гейт «число-в-число» для ядра tools/tk463-sim (Rust) против эталона tk461_scan/tk461_ctrl.
python tk463_gate.py cells <out.csv>                         — спецификация ячеек D2a (K.CELLS): stop,m,take,par
python tk463_gate.py ctrl-entries <минуты.csv.gz> <tf> <events.csv.gz> <out.csv>  — входы контролей (tk461_ctrl.controls, seed 461)
python tk463_gate.py cmp <эталон.csv.gz> <новое.csv.gz>     — колонки поимённо; float сравниваются по битам f64 (NaN = NaN), строки равны
Строки сравниваются по порядку; число строк и набор колонок обязаны совпасть. Код выхода 0 — совпало, 1 — расхождение."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def cmp(ref, new):
    a, b = pd.read_csv(ref), pd.read_csv(new)
    if len(a) != len(b):
        print(f"ЧИСЛО СТРОК: эталон {len(a)} новое {len(b)}")
        return 1
    if set(a.columns) != set(b.columns):
        print("КОЛОНКИ:", sorted(set(a.columns) ^ set(b.columns)))
        return 1
    bad = 0
    for c in a.columns:
        x, y = a[c], b[c]
        if x.dtype.kind == "f" or y.dtype.kind == "f":
            xf, yf = x.to_numpy(float), y.to_numpy(float)
            neq = ~((xf.view("int64") == yf.view("int64")) | (np.isnan(xf) & np.isnan(yf)))
        else:
            neq = (x.astype(str) != y.astype(str)).to_numpy()
        n = int(neq.sum())
        if n:
            i = int(np.flatnonzero(neq)[0])
            print(f"РАСХОЖДЕНИЕ {c}: {n} строк, первая #{i}: эталон {x.iloc[i]!r} новое {y.iloc[i]!r}")
            bad += n
    print("IDENTICAL" if not bad else f"РАЗЛИЧИЙ: {bad}", len(a), "строк")
    return int(bool(bad))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "cells":
        import tk461_scan as K
        pd.DataFrame([dict(stop=s, m=m, take=t, par=r) for s, m, t, r in K.CELLS]).to_csv(sys.argv[2], index=False)
    elif cmd == "ctrl-entries":
        import tk461_ctrl as C
        C.controls(sys.argv[2], int(sys.argv[3]), pd.read_csv(sys.argv[4])).to_csv(sys.argv[5], index=False)
    elif cmd == "cmp":
        sys.exit(cmp(sys.argv[2], sys.argv[3]))
    else:
        sys.exit(__doc__)
