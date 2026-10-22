#!/usr/bin/env python3
"""TK-289: центры плато П-18 для финала Б (П-18 §5 / П-14 §5). Читает ТОЛЬКО сито половины А (csv сита), чисел Б не видит.
Правило центра (записано до открытия Б):
  1D-ось — самая длинная связная серия клеток с plateau=1 по рангу значения оси (ничья — выше средняя ΔSR_A); центр = клетка с индексом ⌊(n−1)/2⌋ серии;
  2D (6в, S × usd_min) — крупнейшая 8-связная компонента клеток plateau=1; центр = клетка, ближайшая к центроиду компоненты в индексах (ранг S, ранг usd_min), ничья — выше ΔSR_A;
  стопка — клетка с наибольшей ΔSR_A среди годных стопок (плато у стопки не определено; вторая стопка — справочно, в Холм не входит);
  ось без плато — теста нет (p = 1).
  python tk289-final-b-centers.py <sieve.csv> <out.csv>"""
import csv, json, sys, collections

src, out = sys.argv[1:3]
KEY = {"1": "to", "2": "stop", "3": "take", "4": "deadline", "5": "ttl", "6a": "usd_min", "6b": "strength_min", "6v": "flow+usd_min", "7": "btc", "8": "sigma_min", "stack": "stack"}


def num(ax, v):
    if ax == "7" and v == "none": return -1e9
    if v.startswith("pct"): return float(v[3:])
    if v.startswith("tr"): return float(v[2:].split("x")[0])
    return float(v)


rows = list(csv.DictReader(open(src, encoding="utf-8")))
by = collections.defaultdict(list)
for r in rows:
    a = json.loads(r["axes"]); r["_a"] = a; r["_dsr"] = float(r["dSR_B1"]); r["_pl"] = r["plateau"] == "1"; r["_ok"] = r["eligible"] == "1"
    by[a["ax"]].append(r)
res = []   # (семья, клетка, размер зоны, всего плато, примечание)
for ax in ("1", "2", "3", "4", "5", "6a", "6b", "7", "8"):
    cs = sorted(by[ax], key=lambda r: num(ax, r["_a"][KEY[ax]]))
    zones, cur = [], []
    for r in cs:
        if r["_pl"]: cur.append(r)
        else:
            if cur: zones.append(cur)
            cur = []
    if cur: zones.append(cur)
    if not zones:
        res.append((ax, None, 0, 0, "нет плато -> p = 1")); continue
    z = max(zones, key=lambda z: (len(z), sum(r["_dsr"] for r in z) / len(z)))
    res.append((ax, z[(len(z) - 1) // 2], len(z), sum(r["_pl"] for r in cs), f"серий {len(zones)}, размеры {sorted((len(q) for q in zones), reverse=True)}"))
# 6в: 2D
cs = by["6v"]
S = sorted({num("6v", r["_a"][KEY["6v"]].split("|")[0]) for r in cs}); U = sorted({0.0 if r["_a"][KEY["6v"]].split("|")[1] == "none" else num("6v", r["_a"][KEY["6v"]].split("|")[1]) for r in cs})
pos = lambda r: (S.index(num("6v", r["_a"][KEY["6v"]].split("|")[0])), U.index(0.0 if r["_a"][KEY["6v"]].split("|")[1] == "none" else num("6v", r["_a"][KEY["6v"]].split("|")[1])))
pl = {pos(r): r for r in cs if r["_pl"]}
seen, comps = set(), []
for p0 in sorted(pl):
    if p0 in seen: continue
    comp, st = [], [p0]; seen.add(p0)
    while st:
        i, j = st.pop(); comp.append((i, j))
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                q = (i + di, j + dj)
                if (di or dj) and q in pl and q not in seen: seen.add(q); st.append(q)
    comps.append(comp)
if comps:
    c = max(comps, key=len); ci = sum(p[0] for p in c) / len(c); cj = sum(p[1] for p in c) / len(c)
    best = min(c, key=lambda p: ((p[0] - ci) ** 2 + (p[1] - cj) ** 2, -pl[p]["_dsr"]))
    res.append(("6v", pl[best], len(c), len(pl), f"компонент {len(comps)}, размеры {sorted((len(q) for q in comps), reverse=True)}, центроид S-ранг {ci:.2f} U-ранг {cj:.2f}"))
else:
    res.append(("6v", None, 0, 0, "нет плато -> p = 1"))
st = sorted((r for r in by["stack"] if r["_ok"]), key=lambda r: -r["_dsr"])
res.append(("stack", st[0] if st else None, 1 if st else 0, 0, "стопка А (макс ΔSR_A); " + ("вторая справочно: " + st[1]["hash"] if len(st) > 1 else "")))
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["family", "hash", "axes", "fraction", "dSR_A", "p_A", "zone_cells", "plateau_cells_axis", "note"])
    for fam, r, zn, npl, note in res:
        w.writerow([fam, r["hash"] if r else "", json.dumps(r["_a"], ensure_ascii=False) if r else "", r["fraction"] if r else "", r["dSR_B1"] if r else "", r["p"] if r else "", zn, npl, note])
        print(fam, (json.dumps(r["_a"], ensure_ascii=False), r["dSR_B1"], r["p"]) if r else "—", zn, npl, note)
if len(st) > 1:
    print("стопка вторая (справочно):", st[1]["hash"], json.dumps(st[1]["_a"], ensure_ascii=False))
