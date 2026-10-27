#!/usr/bin/env python3
"""TK-414: минутные срезы книги Bybit ob200 (linear, сентябрь, TK-415/416) для признаков точек 1/4. Протокол: docs/research/P-TK414-ob200-2026-10-27.md.
Событийно: каждая строка потока разбирается, книга восстанавливается (snapshot/delta по cts); срез минуты m — состояние книги на её конец
(перед первым событием следующей минуты). Посекундных агрегатов нет; минутный срез — только вывод по уже восстановленной книге.
Использование: tk414-obmin.py <корень ob linear> <выход-каталог> <процессов>   → <выход>/<SYM>/<сутки>.csv: минута_epoch,bb,ba,dB25,dA25
dB25/dA25 — суммарный размер уровней (контракты) в пределах 25 бп от середины лучшей цены с каждой стороны."""
import csv, glob, json, os, sys, zipfile
from multiprocessing import Pool

BAND = 25.0


def lines_of(fh):
    tail = b''
    while True:
        buf = fh.read(1 << 24)
        if not buf:
            break
        buf = tail + buf
        parts = buf.split(b'\n')
        tail = parts.pop()
        yield from parts
    if tail.strip():
        yield tail


def one(job):
    zp, out = job
    if os.path.exists(out):
        return out, 0, "есть"
    rows = []
    bids, asks = {}, {}
    cur = None
    n_err = 0

    def snap(m):
        if not bids or not asks:
            return
        bb, ba = max(bids), min(asks)
        if ba <= bb:
            return
        mid = (bb + ba) / 2
        lo, hi = mid * (1 - BAND / 1e4), mid * (1 + BAND / 1e4)
        rows.append((m, bb, ba, sum(q for p, q in bids.items() if p >= lo), sum(q for p, q in asks.items() if p <= hi)))

    zf = zipfile.ZipFile(zp)
    fh = zf.open(zf.namelist()[0])
    prev = 0.0
    for raw in lines_of(fh):
        if not raw.strip():
            continue
        try:
            d = json.loads(raw)
            te = float(d.get('cts') or d['ts'])
            typ = d['type']
            dd = d['data']
        except Exception:
            n_err += 1
            continue
        if te < prev:
            te = prev
        prev = te
        m = int(te // 60000) * 60
        if cur is None:
            cur = m
        if m != cur:
            snap(cur)  # состояние на конец минуты cur
            cur = m
        if typ == 'snapshot':
            bids = {float(p): float(s) for p, s in dd.get('b', [])}
            asks = {float(p): float(s) for p, s in dd.get('a', [])}
        else:
            for p, s in dd.get('b', ()):
                if float(s) == 0.0:
                    bids.pop(float(p), None)
                else:
                    bids[float(p)] = float(s)
            for p, s in dd.get('a', ()):
                if float(s) == 0.0:
                    asks.pop(float(p), None)
                else:
                    asks[float(p)] = float(s)
    if cur is not None:
        snap(cur)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out + ".tmp", "w", newline="") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow(r)
    os.replace(out + ".tmp", out)
    return out, len(rows), "ошибок разбора %d" % n_err


if __name__ == "__main__":
    root, outd, nproc = sys.argv[1], sys.argv[2], int(sys.argv[3])
    jobs = []
    for zp in sorted(glob.glob(os.path.join(root, "*", "*_ob200.data.zip"))):
        b = os.path.basename(zp)
        day, sym = b.split("_")[0], b.split("_")[1]
        jobs.append((zp, os.path.join(outd, sym, day + ".csv")))
    print("файлов", len(jobs), flush=True)
    with Pool(nproc) as p:
        for i, (o, n, s) in enumerate(p.imap_unordered(one, jobs), 1):
            print(i, o, n, s, flush=True)
    print("готово", flush=True)
