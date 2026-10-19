"""TK-314: сетка F2 по П-17 (Г-137, Г-101 стоп, Г-81) по П-14 §11.2/§3.3 из CSV сита F1 (только чтение).
Зона = связные годные клетки (p<=0,20), <=3 зоны по min p; окрестность +-1 сосед F1 по оси; шаг F1/4, округление к
разрешимости (k, W — целые; Q — 1 процентиль; f — 1 бп); <=12 новых точек на ось; дедуп с F1. Без Летописи."""
import csv, json, itertools, collections, math, sys
SRC = "docs/findings/tk-p17-f1-sieve-2026-10-18.csv"
OUT = "docs/findings/tk314-f2-grid-2026-10-19.csv"
RES = {"k": 1, "T_q": 1, "W_s": 1, "cxl_q": 1, "f_bp": 1, "swept_q": 1, "entry": 1, "A_min": 1}     # разрешимость оси
CAP_AX, CAP_CELLS, DIV = 12, 100, 4
# Потолок 100 клеток НА ЗОНУ (TK-278, Судья 16.10): зона > 100 — 100 клеток сетки, ближайших к центру зоны:
# L∞ в шагах F2 (индекс по точкам оси F1∪F2), затем L1, затем лексикографически по значениям осей; руками не режем.
HYP = {"Г-137": None, "Г-101": ("part", "stop"), "Г-81": None, "Г-135": None, "Г-136": None}
CTRL = {"Г-136": ("k", 0.0)}      # контроль k=0: в зону не идёт, но нужен в каждой точке оси A (П-17 §4)
def fmt(names, c):
    return [(int(x) if float(x).is_integer() else x) if n != "entry" else "postsweep%d" % x for n, x in zip(names, c)]
def num(v):
    v = str(v)
    return float(v[9:] if v.startswith("postsweep") else v)
rows = list(csv.DictReader(open(SRC, encoding="utf-8")))
out_rows, summary = [], []
for h, flt in HYP.items():
    rs = [r for r in rows if r["hyp"] == h]
    for r in rs: r["ax"] = json.loads(r["axes"])
    if flt: rs = [r for r in rs if r["ax"][flt[0]] == flt[1]]
    names = [n for n in rs[0]["ax"] if not (flt and n == flt[0])]
    vals = {n: sorted({num(r["ax"][n]) for r in rs}) for n in names}
    idx = lambda r: tuple(vals[n].index(num(r["ax"][n])) for n in names)
    defd = [r for r in rs if not r["итог"].startswith("не определена") and not r["итог"].startswith("контроль")]
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
    if not zones: zones = [[min(defd, key=key)]]      # §11.2 п.1: годных нет — одна определённая клетка (p↑, dSR↓, сутки↓)
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
            axpts[n] = (sorted(pts), sorted(new))
        full = {n: sorted(set(axpts[n][0]) | set(axpts[n][1])) for n in names}
        grid = [tuple(c) for c in itertools.product(*[full[n] for n in names])]
        newc = [c for c in grid if c not in f1set]
        # центр зоны — среднее индексов клеток зоны по точкам оси F1∪F2; расстояние в шагах F2
        cen = [sum(full[n].index(num(r["ax"][n])) for r in z) / len(z) for n in names]
        dist = lambda c: (max(abs(full[n].index(x) - m) for n, x, m in zip(names, c, cen)),
                          sum(abs(full[n].index(x) - m) for n, x, m in zip(names, c, cen)), c)
        keep = set(sorted(grid, key=dist)[:CAP_CELLS])        # <=100 клеток сетки на зону, F1-клетки внутри считаются
        rec = [c for c in newc if c in keep]
        summary.append((h, zi + 1, len(z), min(float(r["p"]) for r in z), {n: edge[n] for n in names if edge[n]},
                        {n: (len(axpts[n][0]), len(axpts[n][1])) for n in names}, len(grid), len(newc), len(keep), len(rec)))
        for c in newc: out_rows.append((h, zi + 1, json.dumps(dict(zip(names, fmt(names, c)))), int(c in keep), 0))
        if h in CTRL:     # контроль k=0 в каждой точке остальных осей рекомендованного состава (сверх потолка)
            cn, cv = CTRL[h]; ci = names.index(cn)
            ctl = {tuple(cv if j == ci else c[j] for j in range(len(names))) for c in keep}
            for c in sorted(ctl - f1set):
                out_rows.append((h, zi + 1, json.dumps(dict(zip(names, fmt(names, c)))), 1, 1)); summary[-1] += ("ctl+",)
w = csv.writer(open(OUT, "w", newline="", encoding="utf-8")); w.writerow(["hyp", "zone", "axes", "rec_le100", "control"])
for o in out_rows: w.writerow(o)
for s in summary: print(*s, sep=" | ")
