#!/usr/bin/env python3
"""TK-414: минутные срезы книги Gate фьючерсов (архив orderbooks TK-415) для признаков точек 1/4. Протокол: docs/research/P-TK414-ob-gate-2026-10-27.md.
Час-файл = snapshot (строки set) + make/take; знак size: + бид, − аск (как в пилоте tk414-ob.py). Книга каждого часа строится с нуля из его set-строк.
Срез минуты m — состояние на её конец (перед первым событием следующей минуты). Выход как у tk414-obmin.py: <выход>/<SYM>/<ГГГГММДДЧЧ>.csv: минута_epoch,bb,ba,dB25,dA25.
Спот Gate (7 колонок: время,side,action,цена,размер,id,merged; side 2 = бид, 1 = аск) — тот же выход, формат строки определяется по числу колонок.
Использование: tk414-obmin-gate.py <need.tsv: файл<TAB>SYM/ГГГГММДДЧЧ.csv> <выход-каталог> <процессов>
need.tsv (tk414-obgate-need.py) — только часы, нужные событиям: [t−минB−360 мин−1 ч, t]; срезы тех же часов от выбора не зависят."""
import csv, gzip, os, sys
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
            f_ = line.split(",")
            if len(f_) == 7:  # спот: время,side(2 бид / 1 аск),action,цена,размер,begin_id,merged
                t, a, p, s, bid = f_[0], f_[2], f_[3], f_[4], f_[1] == "2"
            else:
                t, a, p, s, _, _ = f_
                bid = float(s) > 0
            m = int(float(t)) // 60 * 60
            if cur is None:
                cur = m
            if m != cur:
                snap(cur)
                cur = m
            p = float(p); s = float(s); q = abs(s)
            d = bids if bid else asks
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
    need, outd, nproc = sys.argv[1], sys.argv[2], int(sys.argv[3])
    jobs = []
    for line in open(need, encoding="utf-8"):
        gp, rel = line.rstrip("\n").split("\t")
        jobs.append((gp, os.path.join(outd, rel)))
    print("файлов", len(jobs), flush=True)
    with Pool(nproc) as p:
        for i, (o, n, s) in enumerate(p.imap_unordered(one, jobs, chunksize=4), 1):
            if i % 200 == 0:
                print(i, o, n, s, flush=True)
    print("готово", flush=True)
