"""П-42 §13.8: число конфигураций сетки, положительных в обеих половинах (описательно, не испытание).

  python tools/p42/count_pos.py data/p42v2 data/p42v2-544 → <ROOT>/count-pos.json и печать
Конфигурация = вариант шага 1 (stage1), расширений (ext) и совместной сетки (joint) стороны, включая базу b0; «положительная» — среднее $/сут. > 0 в A и в B;
вторая строка — то же при ≥ 10 сделок в каждой половине. Кандидаты шага «final» (cA_/cB_/u*/slip/best) в счёт не входят.
"""
import json
import os
import sys

os.environ.setdefault("P42_ROOT", "data/p42v2")
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np  # noqa: E402

import lib  # noqa: E402


def count(root):
    roots = [os.path.join(root, d) for d in ("stage1", "ext", "joint") if os.path.isdir(os.path.join(root, d))]
    D, P, T = lib.load_all(roots)
    out = {}
    for side in lib.SIDES:
        names = sorted(D[side])
        pos = [v for v in names if D[side][v][lib.A].mean() > 0 and D[side][v][lib.B].mean() > 0]
        pos10 = [v for v in pos if T[side][v][0] >= 10 and T[side][v][1] >= 10]
        trading = [v for v in names if T[side][v][0] > 0 and T[side][v][1] > 0]
        out[side] = {"configs": len(names), "trading_both_halves": len(trading), "positive_both": len(pos), "positive_both_ge10": len(pos10),
                     "pos_names": pos, "pos10_names": pos10,
                     "positive_A": sum(1 for v in names if D[side][v][lib.A].mean() > 0), "positive_B": sum(1 for v in names if D[side][v][lib.B].mean() > 0)}
    return out


def main():
    for root in sys.argv[1:]:
        res = count(root)
        json.dump(res, open(os.path.join(root, "count-pos.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        for side, v in res.items():
            print("%-16s %-5s конфигураций %3d (с торговлей в обеих половинах %3d) | A>0: %3d  B>0: %3d  оба>0: %3d  оба>0 и ≥10 сделок: %3d" % (
                root, side, v["configs"], v["trading_both_halves"], v["positive_A"], v["positive_B"], v["positive_both"], v["positive_both_ge10"]))


if __name__ == "__main__":
    main()
