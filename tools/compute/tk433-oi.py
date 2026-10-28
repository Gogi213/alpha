#!/usr/bin/env python3
"""TK-433 v10 §4 К2: ΔOI/OI у пика ноги и контролей (OI 5 мин, TK-415: bybit linear, gate futures_usdt; спот — пусто).
  tk433-oi.py <end-start-th{T}.csv> <trades-th{T}.csv> <корень /srv/alpha-sda/tk415/out> <выход.csv>
K2_pre = (OI[p]-OI[p-60]) / OI[p-60]; K2_post = (OI[p+60]-OI[p]) / OI[p]  (p = peak_min, окна 60 мин назначены Судьёй, §4).
Гипотеза К2: OI падает после пика (K2_post<0). Сегмент К1 в csv не хранится -> «последний сегмент» заменён окном «до» (отступление, в отчёт).
Выход = trades-th{T}.csv + K2_pre, K2_post (для tk433-endr.py)."""
import csv, gzip, os, sys
import numpy as np

src, trd, root, out = sys.argv[1:5]
D = list(csv.DictReader(open(src, encoding="utf-8")))
cache = {}


def oi(ex, sym):
    k = (ex, sym)
    if k not in cache:
        fn = f"{root}/{ex}/oi/{sym}.csv.gz"
        if not os.path.exists(fn):
            cache[k] = None
        else:
            T, V = [], []
            with gzip.open(fn, "rt") as f:
                f.readline()
                for ln in f:
                    q = ln.split(",", 2)
                    v = float(q[1])
                    if v > 0:
                        T.append(int(q[0]))
                        V.append(v)
            i = np.argsort(T)
            cache[k] = (np.array(T, np.int64)[i], np.array(V, float)[i])
    return cache[k]


def at(o, t_ms):
    t, v = o
    i = np.searchsorted(t, t_ms, side="right") - 1
    if i < 0 or t_ms - t[i] > 15 * 60_000:  # дыра > 15 мин -> нет
        return np.nan
    return v[i]


rows = []
for r in D:
    pre = post = np.nan
    if (r['биржа'], r['рынок']) in (("bybit", "linear"), ("gate", "futures_usdt")):
        o = oi(r['биржа'], r['инструмент'])
        if o is not None:
            p = int(r['peak_min']) * 60_000
            a, b, c = at(o, p - 3_600_000), at(o, p), at(o, p + 3_600_000)
            pre, post = (b - a) / a, (c - b) / b
    rows.append((r['id'], pre, post))
seen = {}
for i, a, b in rows:
    seen.setdefault(i, (a, b))
tr = list(csv.DictReader(open(trd, encoding="utf-8")))
cols = list(tr[0].keys()) if tr else ["id"]
have = {r["id"] for r in tr}
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(cols + ["K2_pre", "K2_post"])
    for r in tr:
        a, b = seen.get(r["id"], (np.nan, np.nan))
        w.writerow([r[c] for c in cols] + [("%.6g" % a) if a == a else "", ("%.6g" % b) if b == b else ""])
    for i, (a, b) in seen.items():
        if i not in have:
            w.writerow([i] + [""] * (len(cols) - 1) + [("%.6g" % a) if a == a else "", ("%.6g" % b) if b == b else ""])
print("OI: id", len(seen), "с K2:", sum(1 for a, b in seen.values() if b == b), "инструментов без файла:", sum(v is None for v in cache.values()))
