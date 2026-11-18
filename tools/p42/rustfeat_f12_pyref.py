"""TK-611: эталон Python для f12 — `retitr_ftab.build_episodes` (производственный `CandWorker`, маршрут U0) по одному `<side>-sigsx.csv`.

  python3 rustfeat_f12_pyref.py --sigsx <csv> --side long|short --out <eps.parquet> --cache <каталог .b42> --trades <корень aggTrades> --base-date D
                                [--procs 6] [--prod /data/tk594/py]
То же, что делает `retitr_ftab.py build` до таблиц TK-594; нужен для скорости на равных (`rustfeat_f12_bench.py`) и как источник `eps-*.parquet`, если готового нет.
`retitr_ftab.py` берётся из каталога этого файла.
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sigsx", required=True)
    ap.add_argument("--side", required=True, choices=["long", "short"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--base-date", required=True)
    ap.add_argument("--procs", type=int, default=6)
    ap.add_argument("--prod", default="/data/tk594/py")
    a = ap.parse_args()
    import retitr_ftab as rf
    rf.load_prod(a.prod, a.cache, a.trades, base_date=a.base_date)
    rf.build_episodes(a.side, a.sigsx, a.out, a.procs)


if __name__ == "__main__":
    main()
