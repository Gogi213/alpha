#!/usr/bin/env python3
"""TK-291 (П-19 §5, §8): сито половины А (или Б) и плато по осям k(n) — по closes tk291-recalc.py (closes-p19-<мес>.json).
ΔSR_норм = SR(клетка·m) − SR(B1) по календарным суткам UTC (все 273 сутки 01.01–30.09, без сделок = 0); m = Σ нотионал B1 / Σ нотионал клетки
на (клетка × месяц) по сделкам выбранной половины (М-04 §4, П-19 §5); парный блочный бутстреп по суткам (циклический, блок ⌈n^{1/3}⌉, B=4000),
p односторонний по центрированному Δ*. Годна: p ≤ 0,20, сделок ≥ 20, суток со сделками ≥ 10 (П-14 §4). k=0 — отсечка: в сито идёт, плато-соседом не считается.
Плато 1D: клетка и все её соседи по упорядоченной сетке (без k=1 и k=0) «годна»; крайняя клетка — «(у границы)».
  tk291-sieve.py <каталог closes> <выход.csv> [--half A|B] [--boot 4000] [--vec метка …]   (--vec: только эти метки + B1; для стопки)"""
import csv
import datetime
import hashlib
import json
import math
import sys

import numpy as np

a = sys.argv[1:]
cdir, out = a[0], a[1]
opt = lambda n, d: a[a.index(n) + 1] if n in a else d
half, B = opt("--half", "A"), int(opt("--boot", "4000"))
only = [a[i + 1] for i, x in enumerate(a) if x == "--vec"]
isH = lambda s: (int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0) == (half == "A")
d0 = datetime.date(2026, 1, 1)
ND = (datetime.date(2026, 9, 30) - d0).days + 1
UTC = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]
cl = {mo: json.load(open(f"{cdir}/closes-p19-{mo}.json", encoding="utf-8")) for mo in MONTHS}
labels = [k for k in cl[MONTHS[0]] if k != "B1" and (not only or k in only)]
sr = lambda x: x.mean(axis=-1) / np.maximum(x.std(axis=-1, ddof=1), 1e-12)


def series(lab):
    """-> (сутки[ND] $·m, сделок, суток со сделками, по месяцам [(сделок, $_норм, m)])"""
    day, has, n, bm = np.zeros(ND), np.zeros(ND, bool), 0, {}
    for mo in MONTHS:
        t = [x for x in cl[mo][lab] if isH(x[2])]
        nb = sum(x[3] for x in cl[mo]["B1"] if isH(x[2]))
        nc = sum(x[3] for x in t)
        m = (nb / nc) if (lab != "B1" and nc > 0) else 1.0
        s = 0.0
        for ms, usd, sym, _ in t:
            i = (UTC(ms) - d0).days
            if 0 <= i < ND:
                day[i] += usd * m; has[i] = True; n += 1; s += usd * m
        bm[mo] = (len(t), round(s, 2), round(m, 4))
    return day, n, int(has.sum()), bm


b1, b1n, b1d, b1m = series("B1")
blk = math.ceil(ND ** (1 / 3)); nbk = math.ceil(ND / blk)
starts = np.random.default_rng(19).integers(0, ND, size=(B, nbk))
idx = ((starts[:, :, None] + np.arange(blk)[None, None, :]) % ND).reshape(B, -1)[:, :ND]
b1_b, sr_b1 = sr(b1[idx]), sr(b1)
rows, st = [], {}
for lab in labels:
    x, n, nd, bm = series(lab)
    dsr = sr(x) - sr_b1
    db = sr(x[idx]) - b1_b
    se = db.std(ddof=1)
    p = (1 + np.sum((db - dsr) >= dsr)) / (B + 1) if dsr > 1e-9 else 1.0
    status = "не годна: мало сделок/суток" if not (n >= 20 and nd >= 10) else "годна" if p <= 0.20 else "не годна"
    st[lab] = status
    rows.append([lab, n, nd, round(float(dsr), 4), round(float(p), 4), round(float(se), 4), status, round(float(x.sum()), 2)]
                + [v for mo in MONTHS for v in bm[mo]])
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["vec", f"trades_{half}", f"days_{half}", "dSR", "p", "se", "status", "usd_norm"] + [f"{mo}_{k}" for mo in MONTHS for k in ("n", "usd", "m")])
    w.writerows(rows)
print(f"B1 половина {half}: сделок {b1n}, суток {b1d}, SR(сутки) {sr_b1:.4f}, блок {blk}, B={B}; $ B1 по месяцам {[(mo, b1m[mo][1]) for mo in MONTHS]}")
# плато: соседи по оси в упорядоченной сетке без 0 и 1
pl = []
for ax in range(4):
    ks = sorted(float(l[3:]) for l in labels if l.startswith(f"a{ax}k") and float(l[3:]) != 0)
    for i, k in enumerate(ks):
        lab = f"a{ax}k{k:g}"
        if st.get(lab) != "годна":
            continue
        nb = [f"a{ax}k{ks[j]:g}" for j in (i - 1, i + 1) if 0 <= j < len(ks)]
        good = [l for l in nb if st.get(l) == "годна"]
        pl.append([lab, f"{len(good)}/{len(nb)}", ("плато" + (" (у границы)" if len(nb) < 2 else "")) if len(good) == len(nb) else "плато нет"])
if only == []:
    with open(out.replace(".csv", "-plateau.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(["vec", "neighbours_good", "plateau"]); w.writerows(pl)
import collections
print(collections.Counter(r[6].split(":")[0] for r in rows), "минимально обнаруживаемая ΔSR (80% мощности) ≈",
      round(1.68 * float(np.median([r[5] for r in rows])), 4) if rows else None)
for r in pl:
    print(*r)
