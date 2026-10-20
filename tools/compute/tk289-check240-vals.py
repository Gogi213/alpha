#!/usr/bin/env python3
"""TK-289 П-18 ось 8: σ-таблица окна 240 из sig2 (tk289-sigma-pad.sh) против готовой sigma240 эпохи — значения на общих минутах должны совпасть (центр B1 считан на эпохе, окна 60…960 — на sig2).
Печать по месяцам: файлов, с расхождением значений, минут только в эпохе / только в sig2. Выход 0 — расхождений значений нет, 5 — есть."""
import os
import sys

MS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep"]


def rows(p):
    d = {}
    for l in open(p).read().splitlines():
        k, _, v = l.partition(",")
        if k.isdigit():
            d[k] = v
    return d


bad = 0
for m in MS:
    E = f"/data/tk046/{m}/home/alpha/epochs/e-{m}/study/sigma240"
    N = f"/data/tk0289/sig2/sigma240/{m}"
    nf = nd = oo = no = 0
    for f in sorted(os.listdir(E)):
        a, b = rows(f"{E}/{f}"), rows(f"{N}/{f}") if os.path.exists(f"{N}/{f}") else None
        nf += 1
        if b is None:
            print(f"{m} {f}: нет в sig2"); bad += 1; continue
        diff = sum(1 for k in a if k in b and a[k] != b[k])
        nd += diff > 0; bad += diff > 0
        oo += sum(1 for k in a if k not in b); no += sum(1 for k in b if k not in a)
    print(f"{m}: файлов {nf}, значения расходятся в {nd}; минут только в эпохе {oo}, только в sig2 {no}")
sys.exit(5 if bad else 0)
