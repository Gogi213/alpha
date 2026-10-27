#!/usr/bin/env python3
"""TK-414 OI/фандинг как признаки точек 1/4 (Bybit linear). Протокол (заморожен коммитом до счёта): docs/research/P-TK414-oi-funding-ob-2026-10-27.md.
Использование: tk414-oifund.py <flowbybit2-events.csv> <каталог bybit из TK-415 (oi/, funding/)> <выход-events.csv>
Добавляет к событиям linear (spot без OI/фандинга отбрасывается) колонки oi_surge, oi_dir, fz; окно OI = окно B события [t_событие−минB, t_событие]."""
import csv, gzip, os, sys
from bisect import bisect_right
from datetime import datetime, timezone

ev_f, root, out_f = sys.argv[1:4]
H5 = 72  # 360 мин / 5 мин
FUND_BASE_MS = 3 * 86400000
T_SEP4 = int(datetime(2026, 9, 4, tzinfo=timezone.utc).timestamp() * 1000)


def tms(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp() * 1000)


def med(x):
    y = sorted(x)
    return y[len(y) // 2] if y else 0.0


_oi, _fu = {}, {}


def oi_of(sym):
    if sym not in _oi:
        d = {}
        fn = os.path.join(root, "oi", sym + ".csv.gz")
        if os.path.exists(fn):
            with gzip.open(fn, "rt") as f:
                next(f)
                for line in f:
                    p = line.split(",")
                    d[int(p[0]) // 300000] = float(p[1])
        _oi[sym] = d
    return _oi[sym]


def fund_of(sym):
    if sym not in _fu:
        a = []
        fn = os.path.join(root, "funding", sym + ".csv.gz")
        if os.path.exists(fn):
            with gzip.open(fn, "rt") as f:
                next(f)
                for line in f:
                    p = line.split(",")
                    a.append((int(p[0]), float(p[1])))
        a.sort()
        _fu[sym] = ([x[0] for x in a], [x[1] for x in a])
    return _fu[sym]


def oi_feat(sym, t_end, mins, s):
    oi = oi_of(sym)
    be = t_end // 300000
    bs = (t_end - mins * 60000) // 300000
    k = be - bs
    if k < 1 or be not in oi or bs not in oi or oi[bs] <= 0:  # окно короче 2 баров 5 мин
        return "", ""
    d = (oi[be] - oi[bs]) / oi[bs]
    ds = []
    for e in range(bs - H5 + k, bs + 1):  # окна той же длины k баров целиком в [bs-72, bs]
        a = e - k
        if e in oi and a in oi and oi[a] > 0:
            ds.append(abs((oi[e] - oi[a]) / oi[a]))
    if len(ds) < 10:
        return "", ""
    base = med(ds)
    if base <= 0:
        base = sum(ds) / len(ds)
    if base <= 0:
        return "", ""
    return "%.3f" % (abs(d) / base), "%d" % ((d > 0) - (d < 0)) if d == 0 else "%d" % ((1 if d > 0 else -1) * s)


def fund_feat(sym, t, s):
    if t < T_SEP4:
        return ""
    ts, fs = fund_of(sym)
    j = bisect_right(ts, t) - 1
    if j < 0:
        return ""
    lo = bisect_right(ts, t - FUND_BASE_MS)
    base = med([abs(x) for x in fs[lo:j]])  # расчёты строго до последнего
    if base <= 0:
        return ""
    return "%.3f" % (s * fs[j] / base)


def main():
    n = nol = 0
    with open(ev_f, newline="", encoding="utf-8") as fi, open(out_f, "w", newline="", encoding="utf-8") as fo:
        rd = csv.DictReader(fi)
        w = csv.DictWriter(fo, rd.fieldnames + ["oi_surge", "oi_dir", "fz"])
        w.writeheader()
        for r in rd:
            if r["рынок"] != "linear":
                continue
            t = tms(r["t_событие"])
            s = int(r["знак"])
            sym = r["инструмент"]
            r["oi_surge"], r["oi_dir"] = oi_feat(sym, t, int(r["минB"]) if r["минB"] else 0, s)
            r["fz"] = fund_feat(sym, t, s)
            n += 1
            nol += r["oi_surge"] == ""
            w.writerow(r)
    print("событий linear", n, "без OI-признака", nol, flush=True)


if __name__ == "__main__":
    main()
