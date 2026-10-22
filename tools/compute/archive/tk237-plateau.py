#!/usr/bin/env python3
"""TK-237: плато (П-14 §4 п. 4) по таблице сита клеток (половина А) + покрытие сетки.
Соседи — по упорядоченным значениям оси в полной сетке F1 (coverage). Сосед без результата сита
(старые 47 клеток / не посчитана / «не определена») = «нет данных»; если с ними плато достижимо — «не определено», иначе «плато нет».
  python tk237-plateau.py <sieve.csv> <coverage.csv> <out.csv>"""
import csv, json, sys, collections
from fractions import Fraction
sv, cov, out = sys.argv[1:4]
st = {r["hash"]: r["status"] for r in csv.DictReader(open(sv, encoding="utf-8"))}
cells = list(csv.DictReader(open(cov, encoding="utf-8")))
num = lambda s: Fraction(s)
CAT = {"Г-104": {"be"}, "Г-114": {"trigger"}}
res = []
byh = collections.defaultdict(list)
for r in cells: byh[r["hyp"]].append((r["hash"], json.loads(r["axes"])))
for hyp, lst in byh.items():
    axes = sorted({k for _, a in lst for k in a})
    cat = CAT.get(hyp, set()); numax = []
    for k in axes:
        vals = {a[k] for _, a in lst if k in a}
        if k in cat: continue
        try: sorted(vals, key=num); numax.append(k)
        except Exception: cat.add(k)
    if hyp == "Г-117" or not numax: pass
    ordv = {k: sorted({a[k] for _, a in lst if k in a}, key=num) for k in numax}
    idx = {tuple(sorted(a.items())): h for h, a in lst}
    for h, a in lst:
        if h not in st: continue                     # только 320 новых хешей
        if st[h] != "годна": res.append([hyp, h, json.dumps(a, ensure_ascii=False), st[h], "", "", ""]); continue
        # соседи: ±1 по каждой числовой оси (1D) / 8-окрестность (2D), категории фиксированы
        pos = {k: ordv[k].index(a[k]) for k in numax}; nb = []
        import itertools
        for d in itertools.product([-1, 0, 1], repeat=len(numax)):
            if not any(d): continue
            if len(numax) == 1 or True:
                b = dict(a); ok = True
                for k, dd in zip(numax, d):
                    j = pos[k] + dd
                    if j < 0 or j >= len(ordv[k]): ok = False; break
                    b[k] = ordv[k][j]
                if ok: nb.append(idx.get(tuple(sorted(b.items()))))
        exist = [x for x in nb if x]; good = [x for x in exist if st.get(x) == "годна"]
        need = (len(exist) // 2 + 1) if len(numax) > 1 else len(exist)
        edge = len(exist) < (8 if len(numax) > 1 else 2)
        nodata = [x for x in exist if x not in st or st[x].startswith("не определена")]
        plateau = len(exist) > 0 and len(good) >= need and not (len(numax) == 1 and len(exist) == 1 and False)
        res.append([hyp, h, json.dumps(a, ensure_ascii=False), "годна",
                    f"{len(good)}/{len(exist)}", f"нет данных у {len(nodata)}" if nodata else "", ("плато" + (" (у границы)" if edge else "")) if plateau else ("не определено (сосед без данных)" if len(good) + len(nodata) >= need else "плато нет")])
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["hyp", "hash", "axes", "status", "neighbours_good", "note", "plateau"]); w.writerows(res)
for r in res:
    if r[3] == "годна": print(r[0], r[2], r[4], r[5], r[6])
