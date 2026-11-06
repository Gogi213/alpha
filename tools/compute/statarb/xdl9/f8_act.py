#!/usr/bin/env python3
"""TK-493 w5 F (П-37 r3.2 D5 iii, аудит M3-01 / M3-04): активные пары-сутки D2 ПО ПАНЕЛЯМ для всех групп семейства (основной счёт, плацебо-2, случайные пары, «без 17»).

Определение (d2_active.py): сутки пары активны, если open ОБЕИХ ног конечен хотя бы в одну минуту суток И к концу суток накоплено >= k выплат ряда d. Знаменатель D2
в отчёте F8 и в статусе - число таких пар-суток; z ядра для D2 не показатель. Числитель - сделки тех же пар (агрегатор считает ещё и «сироты» по (задание, сутки)).

  python f8_act.py --groups act/main.tsv --d0 2479 --d1 2500 --out out/act/main [--procs 8]
      groups - tsv `stem, cell, panels_y, panels_x, dir, k` (пути могут содержать $HOME); dir - каталог группы d2_diff_funding.py (manifest.csv + shardN/);
      выход `<out>/<stem>.act.csv`: cell,y,x,day,active. Запись атомарная (.part -> rename). numpy (Deck: ~/xdl9/venv).
  python f8_act.py selftest

Читает панели и ряды d на сутках [d0, d1): на боевом запуске это торговое окно - только по команде CEO (стадия act списка единиц F8).
"""
import csv, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def one_group(row, d0, d1, out):
    import d2_active as A
    stem, cell, py, px, gdir, k = row
    py, px, gdir = (os.path.expandvars(os.path.expanduser(x)) for x in (py, px, gdir))
    if not os.path.exists(os.path.join(gdir, "manifest.csv")):
        return stem, -1
    res = A.group_active(py, px, gdir, int(k), d0, d1)
    path = os.path.join(out, stem + ".act.csv")
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["cell", "y", "x", "day", "active"])
        for y, x, act in res:
            for d, v in zip(range(d0, d1), act):
                w.writerow([cell, y, x, d, v])
    os.replace(tmp, path)
    return stem, len(res)


def read_groups(path):
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh, delimiter="\t")
        next(rd)
        return [tuple(r) for r in rd if r]


def run(a):
    rows = read_groups(a["groups"])
    os.makedirs(a["out"], exist_ok=True)
    args = [(r, a["d0"], a["d1"], a["out"]) for r in rows]
    if a["procs"] > 1 and len(rows) > 1:
        from multiprocessing import Pool
        with Pool(min(a["procs"], len(rows))) as p:
            res = p.starmap(one_group, args)
    else:
        res = [one_group(*x) for x in args]
    bad = [s for s, n in res if n < 0]
    print("act: групп %d, пар %d, групп без manifest.csv %d" % (len(res), sum(n for _, n in res if n > 0), len(bad)))
    if bad:
        print("ОШИБКА: нет manifest.csv у групп: " + ", ".join(bad[:5]))
        return 1
    return 0


def selftest():
    import gzip, struct, tempfile
    import numpy as np
    GRID0_MS = 1_577_836_800_000
    with tempfile.TemporaryDirectory() as td:
        def sap(path, first, op):
            with open(path, "wb") as f:
                f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first, len(op)))
                np.asarray(op, "<f8").tofile(f)
                np.asarray(op, "<f8").tofile(f)
        d0 = 2479
        n = 2 * 1440
        py, px = os.path.join(td, "py"), os.path.join(td, "px")
        os.makedirs(py)
        os.makedirs(px)
        oy = np.full(n, 10.0)
        ox = np.full(n, 20.0)
        ox[:1440] = np.nan                       # X не торгуется в первые сутки
        sap(os.path.join(py, "Y.sap"), d0 * 1440, oy)
        sap(os.path.join(px, "X.sap"), d0 * 1440, ox)
        g = os.path.join(td, "g")
        os.makedirs(os.path.join(g, "shard0"))
        with open(os.path.join(g, "manifest.csv"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("y,x,shard,status\nY,X,0,ok\n")
        with gzip.open(os.path.join(g, "shard0", "Y.csv.gz"), "wt", encoding="utf-8", newline="") as fh:
            fh.write("time_ms,rate,interval_h\n")
            for m in (d0 * 1440 + 100, d0 * 1440 + 500, (d0 + 1) * 1440 + 100):
                fh.write("%d,0.0001,8\n" % (GRID0_MS + m * 60_000))
        r = one_group(("S", "D2|a~b|crypto|a0", py, px, g, "2"), d0, d0 + 2, td)
        assert r == ("S", 1), r
        rows = list(csv.DictReader(open(os.path.join(td, "S.act.csv"), encoding="utf-8")))
        # сутки 0: X нет (NaN) -> 0 (и меток 2 >= k=2 есть); сутки 1: обе ноги есть, меток накоплено 3 >= 2 -> 1
        assert [(int(x["day"]), int(x["active"])) for x in rows] == [(d0, 0), (d0 + 1, 1)], rows
        r2 = one_group(("T", "D2|a~b|crypto|a0", py, px, os.path.join(td, "nope"), "2"), d0, d0 + 2, td)
        assert r2 == ("T", -1)
    print("selftest ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    else:
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("--groups", required=True)
        ap.add_argument("--d0", type=int, required=True)
        ap.add_argument("--d1", type=int, required=True)
        ap.add_argument("--out", required=True)
        ap.add_argument("--procs", type=int, default=1)
        raise SystemExit(run(vars(ap.parse_args())))
