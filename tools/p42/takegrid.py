"""П-42 §13.12: клетки сетки тейков (перекос цены γ × перекос объёма κ, с `pk` и без) и их строки вариантов для `breakout tick`.

  python3 tools/p42/takegrid.py stage1   → строки вариантов шага 1 (24 клетки сетки + `ns=1` на базах) на stdout
  python3 tools/p42/takegrid.py ns <id> <id>…  → строки `ns=1` для клеток <id> (результат `takegrid_report.py select`)
"""
import sys

GAMMAS = [0.5, 1, 2]
KAPPAS = [-0.75, 0, 1, 3]
GI0, KI0 = GAMMAS.index(1), KAPPAS.index(0)  # индексы базовой клетки
LAT = "lat=4 latx=4"


def tag(x):
    return ("%g" % x).replace(".", "p").replace("-", "m")


def cell_id(gi, ki, pk=False, ns=False):
    if (gi, ki) == (GI0, KI0):
        s = "b0pk" if pk else "b0"
    else:
        s = "g%sk%s%s" % (tag(GAMMAS[gi]), tag(KAPPAS[ki]), "_pk" if pk else "")
    return s + ("_ns1" if ns else "")


def line(gi, ki, pk=False, ns=False):
    kv = [LAT]
    if pk:
        kv.append("pk=1")
    if GAMMAS[gi] != 1:
        kv.append("tpg=%g" % GAMMAS[gi])
    if KAPPAS[ki] != 0:
        kv.append("tpk=%g" % KAPPAS[ki])
    if ns:
        kv.append("ns=1")
    return "%s %s" % (cell_id(gi, ki, pk, ns), " ".join(kv))


def stage1_lines():
    out = []
    for pk in (False, True):
        for gi in range(len(GAMMAS)):
            for ki in range(len(KAPPAS)):
                out.append(line(gi, ki, pk))
    out.append(line(GI0, KI0, False, True))
    out.append(line(GI0, KI0, True, True))
    return out


def by_id():
    """id клетки → (gi, ki, pk, ns)."""
    d = {}
    for pk in (False, True):
        for ns in (False, True):
            for gi in range(len(GAMMAS)):
                for ki in range(len(KAPPAS)):
                    d[cell_id(gi, ki, pk, ns)] = (gi, ki, pk, ns)
    return d


def ns_lines(ids):
    d = by_id()
    out = []
    for i in ids:
        gi, ki, pk, ns = d[i]
        assert not ns
        out.append(line(gi, ki, pk, True))
    return out


if __name__ == "__main__":
    if sys.argv[1] == "stage1":
        print("\n".join(stage1_lines()))
    elif sys.argv[1] == "ns":
        print("\n".join(ns_lines(sys.argv[2:])))
