#!/usr/bin/env python3
"""TK-439: все 10 вол-файлов (D × знак) одним пулом вместо 10 последовательных запусков tk439-vol.py: задание = инструмент (крупные по числу
событий первыми), серия инструмента строится один раз на все файлы; выход каждого файла — как у tk435-vol.main (те же строки, сортировка, округление).
  tk439-volall.py <windows.csv> <xq.json> <выходной каталог> --cube <куб> --cut <мин> [--procs N]   → vol-D<D>{p|m}.csv"""
import csv
import io
import time
import importlib.util
import os
import sys
from collections import defaultdict
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location("tk439v", os.path.join(HERE, "tk439-vol.py"))
V = importlib.util.module_from_spec(sp)
sys.modules["tk439v"] = V
sp.loader.exec_module(V)
M = V.M
SC, LOAD0 = M.Series, M.load
_C = {}
LT = chr(10)


def _load(roots, sub, sym):
    if (sub, sym) not in _C:
        _C.clear()
        _C[(sub, sym)] = LOAD0(roots, sub, sym)
    return _C[(sub, sym)]


M.load = _load


def _series(*ld):
    k = ("S", id(ld[1]))
    if k not in _C:
        _C[k] = SC(*ld)
    return _C[k]


M.Series = _series


def _fmt(rs):
    """Строки файла форматируются в воркере (запись 83 тыс. × 141 в главном процессе = 28 с); ячейки — как у DictWriter."""
    out, cols = [], None
    b = io.StringIO()
    w = csv.writer(b, lineterminator=LT)
    for r in rs:
        if cols is None:
            cols = tuple(r)
        elif tuple(r) != cols:
            raise ValueError("разные колонки в строках одного файла")
        w.writerow(["" if v is None else (round(v, 8) if isinstance(v, float) else v) for v in r.values()])
        out.append((r["биржа_рынок"], r["инструмент"], r["t_start_ms"], b.getvalue()))
        b.seek(0)
        b.truncate()
    return cols, out


def run(job):
    sub, sym, parts, roots = job
    t = time.time()
    r = [(k, _fmt(V.one((sub, sym, evs, roots)))) for k, evs in parts]
    print('job %s %s %d ev %.1f s' % (sub, sym, sum(len(e) for _, e in parts), time.time() - t), file=sys.stderr)
    return r


def main():
    av = sys.argv
    M.CUBE = av[av.index("--cube") + 1]
    M.CUT = int(av[av.index("--cut") + 1])
    procs = int(av[av.index("--procs") + 1]) if "--procs" in av else 15
    wf, xqf, outd = av[1:4]
    import json
    xq = json.load(open(xqf))
    specs = []  # (ключ, D, X, имя файла)
    for D in (90, 180, 360, 720, 1440):
        for sg, nm in (("+", "p"), ("-", "m")):
            specs.append(((D, nm), D, float(xq["%d|%s" % (D, sg)]["q99"]), "vol-D%d%s.csv" % (D, nm)))
    wins = list(csv.DictReader(open(wf, encoding="utf-8")))
    by = defaultdict(lambda: defaultdict(list))
    for k, D, X, _ in specs:
        for r in wins:
            if r["исключено"] == "" and r["представитель"] == "1" and int(r["D"]) == D and float(r["X"]) == X:
                by[("%s-%s" % (r["биржа"], r["рынок"]), r["инструмент"])][k].append(r)
    jobs = [(s, i, sorted(p.items()), []) for (s, i), p in sorted(by.items())]
    jobs.sort(key=lambda j: -sum(len(e) for _, e in j[2]))
    rows, hdr = defaultdict(list), {}
    t0 = time.time()
    with Pool(procs) as pool:
        for res in pool.imap_unordered(run, jobs, chunksize=1):
            for k, (cols, out) in res:
                if cols is not None:
                    if hdr.setdefault(k, cols) != cols:
                        raise ValueError("разные колонки у инструментов файла %s" % (k,))
                rows[k] += out
    print('пул %.1f с, заданий %d' % (time.time() - t0, len(jobs)), file=sys.stderr)
    os.makedirs(outd, exist_ok=True)
    for k, D, X, fn in specs:
        rs = sorted(rows[k], key=lambda r: r[:3])
        with open(os.path.join(outd, fn), "w", newline="", encoding="utf-8") as f:
            if rs:
                b = io.StringIO()
                csv.writer(b, lineterminator=LT).writerow(hdr[k])
                f.write(b.getvalue())
            f.writelines(r[3] for r in rs)
        print(fn, len(rs), "событий")
    print('всего с пула %.1f с' % (time.time() - t0), file=sys.stderr)


if __name__ == "__main__":
    main()
