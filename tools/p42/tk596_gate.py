"""П-42 §13.22 (TK-596): ворота до чтения и счёт сделок через выплату фандинга. Печатают только совпадение / число — суммы и цифры `S_es15` не показывают.

  python3 tk596_gate.py g3 --new <каталог прогона августа TK-596> --ref <каталог прогона rt-aug TK-595> [--skip 0GUSDT,龙虾USDT]
      G3: сделки L_base (лонг) и S_base (шорт) монет, кроме --skip, совпадают по (sym, entry_ms, exit_ms, net) — множество и число; иначе код 1.
  python3 tk596_gate.py cross --run <каталог прогона> --cells S_es15:short,L_base:long,S_base:short
      D1: сколько сделок держали позицию через момент выплаты фандинга (00/08/16 UTC от P42_T0 = полночь UTC: момент строго позже входа и строго раньше выхода).
"""
import argparse
import csv
import os
import sys

FUND_MS = 8 * 3600 * 1000


def trades(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def key(r):
    return (r["sym"], int(r["entry_ms"]), int(r["exit_ms"]), r["net"])


def g3(new, ref, skip):
    ok = True
    for cell, side in (("L_base", "long"), ("S_base", "short")):
        a = sorted(key(r) for r in trades(os.path.join(new, cell, side + "-trades.csv")) if r["sym"] not in skip)
        b = sorted(key(r) for r in trades(os.path.join(ref, cell, side + "-trades.csv")) if r["sym"] not in skip)
        same = a == b
        ok &= same
        print("G3 %s (%s): сделок %d против %d (без %s) — %s" % (cell, side, len(a), len(b), ",".join(sorted(skip)), "совпало" if same else "НЕ СОВПАЛО"))
    return 0 if ok else 1


def crosses(r):
    e, x = int(r["entry_ms"]), int(r["exit_ms"])
    return ((e // FUND_MS) + 1) * FUND_MS < x


def cross(run, cells):
    for c in cells.split(","):
        cell, side = c.split(":")
        t = trades(os.path.join(run, cell, side + "-trades.csv"))
        print("D1 %s (%s): сделок %d, через момент выплаты %d" % (cell, side, len(t), sum(1 for r in t if crosses(r))))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    g = sub.add_parser("g3")
    g.add_argument("--new", required=True)
    g.add_argument("--ref", required=True)
    g.add_argument("--skip", default="0GUSDT,龙虾USDT")
    c = sub.add_parser("cross")
    c.add_argument("--run", required=True)
    c.add_argument("--cells", required=True)
    a = ap.parse_args(argv)
    if a.mode == "g3":
        return g3(a.new, a.ref, set(a.skip.split(",")))
    return cross(a.run, a.cells)


if __name__ == "__main__":
    sys.exit(main())
