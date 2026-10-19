"""TK-314: сетка F2 по П-17 (Г-137, Г-101 стоп, Г-81) по П-14 §11.2/§3.3 из CSV сита F1 (только чтение).
Зона = связные годные клетки (p<=0,20), <=3 зоны по min p; окрестность +-1 сосед F1 по оси; шаг F1/4, округление к
разрешимости (k, W — целые; Q — 1 процентиль; f — 1 бп); <=12 новых точек на ось; дедуп с F1. Без Летописи."""
import csv, json, itertools, collections, math, sys
SRC = "docs/findings/tk-p17-f1-sieve-2026-10-18.csv"
OUT = "docs/findings/tk314-f2-grid-2026-10-19.csv"
RES = {"k": 1, "T_q": 1, "W_s": 1, "cxl_q": 1, "f_bp": 1}     # разрешимость оси
CAP_AX, CAP_CELLS, DIV = 12, 100, 4
# рекомендация под потолок 100 клеток/гипотезу (П-14 §11.2 п.4): новых точек на ось, ближе к центру зоны
CAPS = {("Г-101", 1): {"T_q": 5, "f_bp": 6}, ("Г-81", 1): {"W_s": 5, "cxl_q": 4}}
HYP = {"Г-137": None, "Г-101": ("part", "stop"), "Г-81": None}
def num(v): return float(v)
rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
out_rows, summary = [], []
for h, flt in HYP.items():
    rs = [r for r in rows if r["hyp"] == h]
    for r in rs: r["ax"] = json.loads(r["axes"])
    if flt: rs = [r for r in rs if r["ax"][flt[0]] == flt[1]]
    names = [n for n in rs[0]["ax"] if not (flt and n == flt[0])]
    vals = {n: sorted({num(r["ax"][n]) for r in rs}) for n in names}
    idx = lambda r: tuple(vals[n].index(num(r["ax"][n])) for n in names)
    defd = [r for r in rs if r["итог"] != "не определена"]
    good = [r for r in defd if r["eligible"] == "1"]
    key = lambda r: (float(r["p"]), -float(r["dSR_B1"]), -int(r["days_A"]))
    pos = {idx(r): r for r in good}
    seen, zones = set(), []
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
    f1set = {tuple(num(r["ax"][n]) for n in names) for r in rs}
    for zi, z in enumerate(zones):
        axpts, edge, capn = {}, {}, {}
        for k, n in enumerate(names):
            ii = sorted({idx(r)[k] for r in z})
            lo, hi = max(ii[0] - 1, 0), min(ii[-1] + 1, len(vals[n]) - 1)
            edge[n] = "верх" if ii[-1] == len(vals[n]) - 1 else ("низ" if ii[0] == 0 else "")
            pts = set(vals[n][lo:hi + 1]); res = RES[n]
            centre = sum(vals[n][i] for i in ii) / len(ii)
            new = []
            for a, b in zip(vals[n][lo:hi], vals[n][lo + 1:hi + 1]):
                step = max((b - a) / DIV, res)
                step = round(step / res) * res or res
                x = a + step
                while x < b - 1e-9: new.append(round(x, 6)); x += step
            new = sorted(set(new) - pts, key=lambda x: abs(x - centre))[:CAP_AX]
            axpts[n] = (sorted(pts), sorted(new)); capn[n] = sorted(new, key=lambda x: abs(x - centre))[:CAPS.get((h, zi + 1), {}).get(n, CAP_AX)]
        grid = [tuple(c) for c in itertools.product(*[sorted(set(p) | set(q)) for p, q in axpts.values()])]
        newc = [c for c in grid if c not in f1set]
        summary.append((h, zi + 1, len(z), min(float(r["p"]) for r in z), {n: edge[n] for n in names if edge[n]},
                        {n: (len(axpts[n][0]), len(axpts[n][1])) for n in names}, len(grid), len(newc)))
        capg = set(itertools.product(*[sorted(set(axpts[n][0]) | set(capn[n])) for n in names]))
        rec = [c for c in newc if c in capg]
        summary[-1] += (len(capg), len(rec))
        for c in newc: out_rows.append((h, zi + 1, json.dumps(dict(zip(names, c))), int(c in capg)))
w = csv.writer(open(OUT, "w", newline="", encoding="utf-8")); w.writerow(["hyp", "zone", "axes", "rec_le100"])
for o in out_rows: w.writerow(o)
for s in summary: print(*s, sep=" | ")
