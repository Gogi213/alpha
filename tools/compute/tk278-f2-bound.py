"""TK-278 п.4: верхняя граница объёма F2 по П-14 §11.2 на таблице сита по всем 50 гипотезам (копия tk269-f2-bound.py с аргументами). Только чтение CSV; без дедупа по Летописи и без разрешимости.
  python tk278-f2-bound.py <sieve.csv> <plateau.csv> <out.csv>"""
import csv, json, itertools, collections, sys
SRC, PLT, OUT = sys.argv[1:4]
HASPL = {r["hyp"] for r in csv.DictReader(open(PLT, encoding="utf-8")) if r["plateau"] in ("плато", "плато (у границы)")}
CAT = {"trigger", "be"}      # категориальные оси (§4 п. 4)
CAP_AX, CAP_CELLS, DIV, MONTHS = 12, 100, 4, 9
def fv(x):
    s = str(x)
    if "/" in s:
        a, b = s.split("/"); return float(a) / float(b)
    try: return float(s)
    except ValueError: return None
rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
by = collections.defaultdict(list)
for r in rows:
    r["ax"] = json.loads(r["axes"]); r["good"] = r["status"] == "годна"
    r["defd"] = r["status"] in ("годна", "не годна")
    by[r["hyp"]].append(r)
out = []
for h, rs in sorted(by.items()):
    if not any(r["defd"] for r in rs): continue
    names = list(rs[0]["ax"])
    vals = {n: sorted({r["ax"][n] for r in rs}, key=lambda v: (fv(v) is None, fv(v) or 0, v)) for n in names}
    idx = lambda r: tuple(vals[n].index(r["ax"][n]) for n in names)
    key = lambda r: (float(r["p"]), -float(r["dSR"]), -int(r["days_A"]))
    good = [r for r in rs if r["good"]]
    if good:
        seen, zones = set(), []
        pos = {idx(r): r for r in good}
        for i0 in pos:
            if i0 in seen: continue
            comp, st = [], [i0]; seen.add(i0)
            while st:
                c = st.pop(); comp.append(pos[c])
                for d in itertools.product((-1, 0, 1), repeat=len(names)):
                    n = tuple(a + b for a, b in zip(c, d))
                    if n in pos and n not in seen: seen.add(n); st.append(n)
            zones.append(comp)
        zones = sorted(zones, key=lambda z: min(map(key, z)))[:3]
    else:
        zones = [[min((r for r in rs if r["defd"]), key=key)]]
    tot_cells = tot_new = 0
    for z in zones:
        pts_n, f1_n = [], []
        for k, n in enumerate(names):
            ii = sorted({idx(r)[k] for r in z})
            lo, hi = max(ii[0] - 1, 0), min(ii[-1] + 1, len(vals[n]) - 1)
            f1 = hi - lo + 1
            new = 0 if n in CAT else min((f1 - 1) * (DIV - 1), CAP_AX)
            pts_n.append(f1 + new); f1_n.append(f1)
        cells = 1
        for p in pts_n: cells *= p
        f1c = 1
        for p in f1_n: f1c *= p
        tot_cells += cells; tot_new += min(cells, CAP_CELLS) - f1c if cells <= CAP_CELLS else CAP_CELLS - f1c
    out.append((h, len(zones), "годные" if good else "нет годных", "плато" if h in HASPL else "плато нет", sum(r["defd"] for r in rs), len(good), tot_cells, max(tot_new, 0), max(tot_new, 0) * MONTHS))
w = csv.writer(open(OUT, "w", newline="", encoding="utf-8"))
w.writerow(["hyp", "zones", "zone_basis", "f1_plateau", "defined_cells", "good_cells", "cells_f2_upper", "new_hashes_upper", "new_hash_months_upper"])
for o in out: w.writerow(o)
print(*out, sep="\n"); print("TOTAL", sum(o[7] for o in out), sum(o[8] for o in out))
