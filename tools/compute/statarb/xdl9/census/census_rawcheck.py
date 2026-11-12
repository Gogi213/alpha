#!/usr/bin/env python3
"""TK-493, T1: проверка по СЫРЬЮ (не по панелям): для крупнейших эпизодов theta берёт сырые минутные свечи обеих ног (~/xdl/out/<биржа>/candles/<SYM>.csv.gz)
вокруг начала эпизода и печатает o, h, l, c, v. Показывает, что close в панели - реальная сделка с объёмом, и видно, тень это или тело свечи.
  census_rawcheck.py --out <каталог census_tails> --theta 1000 --n 6 [--before 2] [--after 4]
Только numpy и stdlib."""
import argparse
import csv
import datetime
import gzip
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import census_tails as C   # noqa: E402

GRID0_MS = 1_577_836_800_000


def raw_rows(raw_root, ex, sym, ms_lo, ms_hi):
    p = os.path.join(raw_root, ex, "candles", sym + ".csv.gz")
    out = []
    with gzip.open(p, "rt", encoding="utf-8", newline="") as f:
        next(f, None)
        for line in f:
            q = line.strip().split(",")
            if len(q) < 6:
                continue
            t = int(q[0])
            if t < ms_lo:
                continue
            if t > ms_hi:
                break
            out.append((t, q[1], q[2], q[3], q[4], q[5]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw-root", default=os.path.expanduser("~/xdl/out"))
    ap.add_argument("--theta", type=int, default=1000)
    ap.add_argument("--n", type=int, default=6)
    ap.add_argument("--before", type=int, default=2)
    ap.add_argument("--after", type=int, default=3)
    a = ap.parse_args()
    npz = np.load(os.path.join(a.out, "episodes.npz"))
    E = npz["th%d" % a.theta]
    pairs = list(csv.DictReader(gzip.open(os.path.join(a.out, "pairs.csv.gz"), "rt", encoding="utf-8")))
    E = E[[pairs[int(p)]["univ"] == "c1d2" for p in E["pair"]]]
    E = E[np.argsort(-E["mx"], kind="stable")[:a.n]]
    for e in E:
        p = pairs[int(e["pair"])]
        t0 = int(e["t0"])
        ms0 = GRID0_MS + (C.D0 * C.MIN_DAY + t0) * 60_000
        dt = datetime.datetime.fromtimestamp(ms0 / 1000, datetime.timezone.utc)
        print("== %s %s:%s + %s:%s  начало %s UTC  D=%d  max|dev|=%.0f  знак=%d  gA=%d gB=%d" % (p["key"], p["ex1"], p["sym1"], p["ex2"], p["sym2"], dt.strftime("%F %H:%M"), int(e["D"]), float(e["mx"]), int(e["sign"]), int(e["gA"]), int(e["gB"])))
        lo = ms0 - a.before * 60_000
        hi = ms0 + (int(e["D"]) - 1 + a.after) * 60_000 if int(e["D"]) <= 6 else ms0 + a.after * 60_000
        for ex, sym, tag in ((p["ex1"], p["sym1"], "A"), (p["ex2"], p["sym2"], "B")):
            rows = raw_rows(a.raw_root, ex, sym, lo, hi)
            print("  нога %s %s %s: минут в сырье %d" % (tag, ex, sym, len(rows)))
            for t, o, h, l, c, v in rows:
                print("    %s  o=%s h=%s l=%s c=%s v=%s" % (datetime.datetime.fromtimestamp(t / 1000, datetime.timezone.utc).strftime("%H:%M"), o, h, l, c, v))


if __name__ == "__main__":
    main()
