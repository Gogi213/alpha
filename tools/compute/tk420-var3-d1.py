#!/usr/bin/env python3
"""TK-420 вар. 3, признак D1 «истощение стороны» (протокол docs/research/P-TK420-pump-dynamics-end-month2-2026-10-27.md, утверждён Судьёй).
D1(N) = (d[τ−1] − d[τ−N]) / d[τ−N], d — глубина встречной стороны (ask при s=+1, bid при s=−1) ≤25 бп из минутных срезов книги (конец минут).
Выбранные: D1 ≤ нижнего тертиля (33,3-й процентиль) тех же отношений d[u]/d[u−(N−1) мин]−1 по базе [t−минB−360 мин, t−минB) своей монеты (≥60 значений).
Вход: markout-X.csv (TK-419) + obfeat-events.csv (минB) + корень срезов (A: <SYM>/<сутки>.csv, B: <SYM>/<ГГГГММДДЧЧ>.csv). Выход: markout + d1_5, thr_5, sel_5, d1_15, thr_15, sel_15.
Использование: tk420-var3-d1.py <A|B> <markout.csv> <obfeat-events.csv> <корень срезов> <выход.csv> [процессов=15]"""
import csv, os, sys
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from multiprocessing import Pool

NS = (5, 15)


def load(root, sym):
    d = os.path.join(root, sym)
    ks, dB, dA = [], [], []
    if os.path.isdir(d):
        rows = []
        for fn in sorted(os.listdir(d)):
            with open(os.path.join(d, fn), newline="") as f:
                for line in f:
                    p = line.split(",")
                    rows.append((int(p[0]), float(p[3]), float(p[4])))
        rows.sort()
        ks = [r[0] for r in rows]; dB = [r[1] for r in rows]; dA = [r[2] for r in rows]
    return ks, dB, dA


def work(arg):
    root, sym, evs = arg
    ks, dB, dA = load(root, sym)
    out = []
    kmap = {k: i for i, k in enumerate(ks)}
    for r in evs:
        o = dict(r)
        for N in NS:
            o["d1_%d" % N] = o["thr_%d" % N] = o["sel_%d" % N] = ""
        if not ks:
            out.append(o); continue
        te = int(datetime.strptime(r["t_событие"], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()) // 60 * 60
        dd = dA if r["знак"] == "1" else dB
        for N in NS:
            i1, i0 = kmap.get(te - 60), kmap.get(te - 60 * N)
            if i1 is None or i0 is None or dd[i0] <= 0:
                continue
            d1 = (dd[i1] - dd[i0]) / dd[i0]
            lo = te - 60 * int(r["минB"]) - 21600
            hi = te - 60 * int(r["минB"])
            a, b = bisect_left(ks, lo), bisect_left(ks, hi)
            vals = []
            for j in range(a, b):
                j0 = kmap.get(ks[j] - 60 * (N - 1))
                if j0 is not None and dd[j0] > 0:
                    vals.append(dd[j] / dd[j0] - 1)
            o["d1_%d" % N] = "%.5f" % d1
            if len(vals) >= 60:
                vs = sorted(vals); q = (len(vs) - 1) / 3; k = int(q); th = vs[k] + (vs[min(k + 1, len(vs) - 1)] - vs[k]) * (q - k)
                o["thr_%d" % N] = "%.5f" % th
                o["sel_%d" % N] = "1" if d1 <= th else "0"
        out.append(o)
    return out


def main():
    pop, mo_f, ob_f, root, outp = sys.argv[1:6]
    nproc = int(sys.argv[6]) if len(sys.argv) > 6 else 15
    minb = {}
    for r in csv.DictReader(open(ob_f, newline="", encoding="utf-8")):
        minb[(r["инструмент"], r["t_событие"], r["знак"])] = r["минB"]
    rows = list(csv.DictReader(open(mo_f, newline="", encoding="utf-8")))
    by = {}
    nm = 0
    for r in rows:
        m = minb.get((r["инструмент"], r["t_событие"], r["знак"]))
        if not m:
            nm += 1
            m = "0"
        r["минB"] = m
        by.setdefault(r["инструмент"], []).append(r)
    with Pool(nproc) as p:
        res = [x for chunk in p.imap_unordered(work, [(root, k, v) for k, v in by.items()]) for x in chunk]
    cols = list(res[0].keys())
    with open(outp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(res)
    for N in NS:
        print("N=%d: событий %d, D1 есть %d, выбрано %d, без минB %d" % (N, len(res), sum(1 for x in res if x["d1_%d" % N] != ""), sum(1 for x in res if x["sel_%d" % N] == "1"), nm))


if __name__ == "__main__":
    main()
