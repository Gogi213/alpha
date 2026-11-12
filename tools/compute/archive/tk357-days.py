#!/usr/bin/env python3
"""TK-357: апрель, фьюч, первое касание, страты w20 {500-1000, >=1000}: суммы m_10m/m_1h по (сутки, монета).
  tk357-days.py <out.csv>   env NX (15). Берёт кэш D20 апреля (как g015-ceilings.py), новых проходов книги нет."""
import csv, glob, os, sys
import multiprocessing as mp
FUT = "/data/tk046/apr/home/alpha/epochs/e-apr/study/approaches/D20/2026-*/touches-*USDT.csv"


def fl(x):
    try:
        return float(x)
    except ValueError:
        return None


def work(p):
    sym = os.path.basename(p)[len("touches-"):-4]
    out = {}
    with open(p, newline="") as fh:
        r = csv.reader(fh)
        h = next(r)
        ix = {c: i for i, c in enumerate(h)}
        for x in r:
            if x[ix["touch_index"]] != "0":
                continue
            w = fl(x[ix["strength_w20_pct"]]) or 0.0
            if w < 500:
                continue
            st = "ge1000" if w >= 1000 else "500-1000"
            a, b = fl(x[ix["m_10m"]]), fl(x[ix["m_1h"]])
            c = out.setdefault((st, x[ix["day_utc"]], sym), [0, 0, 0.0, 0, 0.0, 0.0])
            c[0] += 1
            if a is not None:
                c[1] += 1
                c[2] += a
            if b is not None:
                c[3] += 1
                c[4] += b
                c[5] += b * b
    return out


if __name__ == "__main__":
    fs = sorted(glob.glob(FUT))
    with mp.Pool(int(os.environ.get("NX", 15))) as pool:
        res = pool.map(work, fs, chunksize=4)
    with open(sys.argv[1], "w", newline="") as f:
        w = csv.writer(f)
        w.writerow("strata day sym n n10m s10m n1h s1h ss1h".split())
        for o in res:
            for k, c in sorted(o.items()):
                w.writerow([*k, *c])
    print("files", len(fs))
