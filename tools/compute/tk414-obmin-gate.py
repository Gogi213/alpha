#!/usr/bin/env python3
"""TK-414: минутные срезы книги Gate фьючерсов (архив orderbooks TK-415) для признаков точек 1/4. Протокол: docs/research/P-TK414-ob-gate-2026-10-27.md.
Час-файл = snapshot (строки set) + make/take; знак size: + бид, − аск (как в пилоте tk414-ob.py). Книга каждого часа строится с нуля из его set-строк.
Срез минуты m — состояние на её конец (перед первым событием следующей минуты). Выход как у tk414-obmin.py: <выход>/<SYM>/<ГГГГММДДЧЧ>.csv: минута_epoch,bb,ba,dB25,dA25.
Использование: tk414-obmin-gate.py <корень gate-fut> <выход-каталог> <процессов> <инструменты через запятую>"""
import csv, glob, gzip, os, sys
from multiprocessing import Pool

BAND = 25.0


def one(job):
    gp, out = job
    if os.path.exists(out):
        return out, 0, "есть"
    rows = []
    bids, asks = {}, {}
    cur = None

    def snap(m):
        bb = max((p for p, q in bids.items() if q > 0), default=None)
        ba = min((p for p, q in asks.items() if q > 0), default=None)
        if bb is None or ba is None or ba <= bb:
            return
        mid = (bb + ba) / 2
        lo, hi = mid * (1 - BAND / 1e4), mid * (1 + BAND / 1e4)
        rows.append((m, bb, ba, sum(q for p, q in bids.items() if q > 0 and p >= lo), sum(q for p, q in asks.items() if q > 0 and p <= hi)))

    with gzip.open(gp, "rt") as f:
        for line in f:
            t, a, p, s, _, _ = line.split(",")
            m = int(float(t)) // 60 * 60
            if cur is None:
                cur = m
            if m != cur:
                snap(cur)
                cur = m
            p = float(p); s = float(s); q = abs(s)
            d = bids if s > 0 else asks
            if a == "set":
                d[p] = q
            elif a == "make":
                d[p] = d.get(p, 0) + q
            else:
                d[p] = d.get(p, 0) - q
    if cur is not None:
        snap(cur)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out + ".tmp", "w", newline="") as f:
        csv.writer(f).writerows(rows)
    os.replace(out + ".tmp", out)
    return out, len(rows), "ok"


if __name__ == "__main__":
    root, outd, nproc, syms = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4].split(",")
    jobs = []
    for sym in syms:
        for gp in sorted(glob.glob(os.path.join(root, sym, sym + "-*.csv.gz"))):
            jobs.append((gp, os.path.join(outd, sym, os.path.basename(gp)[len(sym) + 1:-7] + ".csv")))
    print("файлов", len(jobs), flush=True)
    with Pool(nproc) as p:
        for i, (o, n, s) in enumerate(p.imap_unordered(one, jobs, chunksize=4), 1):
            if i % 200 == 0:
                print(i, o, n, s, flush=True)
    print("готово", flush=True)
