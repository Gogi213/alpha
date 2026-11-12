#!/usr/bin/env python3
"""TK-417: сверка сделок Bybit сент. (/data/tk417) со свечами 1 м TK-411 (/srv/alpha-sda/candles-1m).
linear: дни 05/15/25 каждого инструмента; spot: весь месяц. На день: число сделок, Σобъём (база), Σquote против свечей
(Σv, Σquote_v по минутам дня), доля минут, где v и quote_v сошлись (отн. 1e-6). n_trades в свечах Bybit API пуст — число сделок
сверять не с чем, печатается только из сделок. Использование: tk417-reconcile.py <каталог сделок> <каталог свечей> <csv выхода>"""
import csv
import glob
import gzip
import os
import sys
from collections import defaultdict

tdir, cdir, outp = sys.argv[1:4]
DAYS = ("05", "15", "25")
D0 = 1788220800  # 2026-09-01 00:00 UTC


def load_candles(path):
    c = {}
    with gzip.open(path, "rt") as f:
        r = csv.reader(f)
        next(r)
        for row in r:
            c[int(row[0]) // 1000] = (float(row[5]), float(row[6]))
    return c


def agg_linear(path):
    m = defaultdict(lambda: [0.0, 0.0])
    n = 0
    with gzip.open(path, "rt") as f:
        next(f)
        for ln in f:
            p = ln.split(",")
            ts = int(float(p[0]))
            a = m[ts // 60 * 60]
            a[0] += float(p[3])
            a[1] += float(p[9])
            n += 1
    return m, n


def agg_spot(path):
    days = {}
    with gzip.open(path, "rt") as f:
        next(f)
        for ln in f:
            p = ln.split(",")
            ts = int(p[1]) // 1000
            d = (ts - D0) // 86400 + 1
            m = days.setdefault(d, [defaultdict(lambda: [0.0, 0.0]), 0])
            a = m[0][ts // 60 * 60]
            v = float(p[3])
            a[0] += v
            a[1] += v * float(p[2])
            m[1] += 1
    return days


def cmp(sym, mk, day, m, n, c):
    lo = D0 + (day - 1) * 86400
    cm = {t: v for t, v in c.items() if lo <= t < lo + 86400}
    tv = sum(a[0] for a in m.values())
    tq = sum(a[1] for a in m.values())
    cv = sum(v[0] for v in cm.values())
    cq = sum(v[1] for v in cm.values())
    ok = 0
    for t, a in m.items():
        b = cm.get(t)
        if b and abs(a[0] - b[0]) <= 1e-6 * max(abs(a[0]), 1e-12) + 1e-9 and abs(a[1] - b[1]) <= 1e-6 * max(abs(a[1]), 1e-12) + 1e-6:
            ok += 1
    mt = len(m)
    return [mk, sym, "2026-09-%02d" % day, n, mt, len(cm), "%.6f" % tv, "%.6f" % cv, "%.6g" % ((tv - cv) / cv if cv else float("nan")),
            "%.6f" % tq, "%.6f" % cq, "%.6g" % ((tq - cq) / cq if cq else float("nan")), "%.4f" % (ok / mt if mt else float("nan"))]


rows = []
for sd in sorted(glob.glob(os.path.join(tdir, "bybit-linear", "*"))):
    sym = os.path.basename(sd)
    cp = os.path.join(cdir, "bybit-linear", sym + ".csv.gz")
    if not os.path.exists(cp):
        rows.append(["linear", sym, "", "", "", "", "", "", "", "", "", "", "нет свечей"])
        continue
    c = load_candles(cp)
    for d in DAYS:
        fp = os.path.join(sd, "%s2026-09-%s.csv.gz" % (sym, d))
        if os.path.exists(fp):
            m, n = agg_linear(fp)
            rows.append(cmp(sym, "linear", int(d), m, n, c))
for sd in sorted(glob.glob(os.path.join(tdir, "bybit-spot", "*"))):
    sym = os.path.basename(sd)
    cp = os.path.join(cdir, "bybit-spot", sym + ".csv.gz")
    fp = os.path.join(sd, "%s-2026-09.csv.gz" % sym)
    if not os.path.exists(cp) or not os.path.exists(fp):
        rows.append(["spot", sym, "", "", "", "", "", "", "", "", "", "", "нет свечей" if not os.path.exists(cp) else "нет сделок"])
        continue
    c = load_candles(cp)
    for d, (m, n) in sorted(agg_spot(fp).items()):
        rows.append(cmp(sym, "spot", d, m, n, c))
with open(outp, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["market", "symbol", "day", "n_trades", "min_trades", "min_candles", "vol_trades", "vol_candles", "vol_rel", "quote_trades",
                "quote_candles", "quote_rel", "min_match"])
    w.writerows(rows)
print("строк", len(rows), flush=True)
