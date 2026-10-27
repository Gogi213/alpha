#!/usr/bin/env python3
"""TK-420 вар. 3, признак D3 «наклон OI» (только A; протокол P-TK420-pump-dynamics-end-month2-2026-10-27.md, утверждён Судьёй; в вердикте — описательно).
D3(N) = s·(OI[be] − OI[bs]) / OI[bs], be = 5-мин бар минуты τ−1, bs = бар на N мин раньше (N=5: 1 бар, N=15: 3 бара); OI — Bybit /v5/market/open-interest 5 мин (TK-415), s — знак события.
Выбранные: D3 ≥ верхнего тертиля (66,7-й процентиль) тех же знаковых изменений для баров-концов в [t−минB−360 мин, t−минB) своей монеты (≥ 30 значений).
Вход: d2-A.csv (tk420-var3-d2.py) -> те же колонки + d3_N, thr3_N, sel_D3_N.
Использование: tk420-var3-d3.py <d2-A.csv> <каталог oi (TK-415, bybit)> <выход.csv>"""
import csv, gzip, os, sys
from datetime import datetime, timezone

NS = (5, 15)
src, root, outp = sys.argv[1:4]
_oi = {}


def oi_of(sym):
    if sym not in _oi:
        d = {}
        fn = os.path.join(root, sym + ".csv.gz")
        if os.path.exists(fn):
            with gzip.open(fn, "rt") as f:
                next(f)
                for line in f:
                    p = line.split(",")
                    d[int(p[0]) // 300000] = float(p[1])
        _oi[sym] = d
    return _oi[sym]


rows = list(csv.DictReader(open(src, newline="", encoding="utf-8")))
for r in rows:
    oi = oi_of(r["инструмент"])
    s = 1 if r["знак"] == "1" else -1
    te = int(datetime.strptime(r["t_событие"], "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).timestamp()) * 1000
    mb = int(r["минB"])
    for N in NS:
        k = N // 5
        r["d3_%d" % N] = r["thr3_%d" % N] = r["sel_D3_%d" % N] = ""
        be = (te - 60000) // 300000
        bs = be - k
        if be not in oi or bs not in oi or oi[bs] <= 0:
            continue
        d3 = s * (oi[be] - oi[bs]) / oi[bs]
        r["d3_%d" % N] = "%.6f" % d3
        lo, hi = (te - (mb + 360) * 60000) // 300000, (te - mb * 60000) // 300000
        vals = [s * (oi[e] - oi[e - k]) / oi[e - k] for e in range(lo, hi) if e in oi and e - k in oi and oi[e - k] > 0]
        if len(vals) >= 30:
            vs = sorted(vals); qq = 2 * (len(vs) - 1) / 3; i = int(qq)
            th = vs[i] + (vs[min(i + 1, len(vs) - 1)] - vs[i]) * (qq - i)
            r["thr3_%d" % N] = "%.6f" % th
            r["sel_D3_%d" % N] = "1" if d3 >= th else "0"
with open(outp, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, list(rows[0].keys())); w.writeheader(); w.writerows(rows)
for N in NS:
    print("N=%d: событий %d, D3 есть %d, выбрано %d" % (N, len(rows), sum(1 for x in rows if x["d3_%d" % N] != ""), sum(1 for x in rows if x["sel_D3_%d" % N] == "1")))
