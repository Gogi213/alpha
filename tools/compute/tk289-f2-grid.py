#!/usr/bin/env python3
"""TK-289 П-18 F2: сетка сужения по П-14 §11.2 из CSV сита F1 (docs/findings/tk289-sieve-f1-2026-10-21.csv).
Зона: годные клетки (p<=0,20) связные по соседству F1; нет годных — одна определённая клетка (p↑, dSR↓, сутки↓). Окрестность = зона + по одному
соседу F1 с каждой стороны по каждой оси (центр B1 — точка оси). Шаг F2 = шаг F1/4 (геометрически, как лог-шаг F1), ≤ 12 новых точек на ось
(ближе к центру зоны), ≤ 100 клеток на гипотезу (2D — сетка окрестности; больше — Судье, не режется). До 3 зон на ось (минимальный p зоны).
Вход: sieve CSV; выход: --out CSV (hyp, axes, kind=new) и печать выписки. Запуск: python tools/compute/tk289-f2-grid.py <sieve.csv> [--out f2-grid.csv]"""
import argparse, csv, json, math, sys
sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from importlib import import_module
P = import_module("p18_plan")

CENTER = {"1": 0.0409, "2": 2.0, "3": 1.0, "4": 14400.0, "5": 1800.0, "7": -44.55, "8": 240.0}
KEY = {"1": "to", "2": "stop", "3": "take", "4": "deadline", "5": "ttl", "6a": "usd_min", "6b": "strength_min", "7": "btc", "8": "sigma_min"}
ND = {"1": 4, "2": 2, "3": 2, "4": 0, "5": 0, "6a": -2, "6b": 0, "7": 1, "8": 0}   # знаков после запятой (к разрешимости; 6a — до 100 $)


def num(ax, s):
    if s == "none":
        return None
    for p in ("pct", "tr"):
        if s.startswith(p):
            s = s[len(p):].split("x")[0]
    return float(s)


def fmt(ax, v):
    d = ND[ax]
    if ax == "2": return "pct%g" % round(v, d)
    if ax == "3": return "tr%gx%g" % (round(v, d), round(v, d))
    return "%g" % (round(v, d) if d > 0 else int(round(v, d)))


def interp(a, b, ax):
    """3 внутренние точки между соседями (шаг/4), геометрически по модулю (знак сохраняется)."""
    sg = -1 if a < 0 else 1
    la, lb = math.log(abs(a)), math.log(abs(b))
    return [sg * math.exp(la + (lb - la) * k / 4) for k in (1, 2, 3)]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("sieve"); ap.add_argument("--out")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.sieve, encoding="utf-8")))
    out, summ = [], {}
    for ax in ["1", "2", "3", "4", "5", "6a", "6b", "7", "8"]:
        rs = [r for r in rows if json.loads(r["axes"])["ax"] == ax]
        vals = []   # (число, строка, ряд|None=центр)
        for r in rs:
            d = json.loads(r["axes"]); v = d[KEY[ax]]
            vals.append((num(ax, v), v, r))
        if ax in CENTER:
            vals.append((CENTER[ax], "center", None))
        if ax == "6a":   # 10k — «нет порога» (центр по пулу), берём как обычную точку
            pass
        num_vals = sorted([x for x in vals if x[0] is not None], key=lambda x: x[0])
        # определённые клетки: >=20 сделок, >=10 суток (годность — столбец eligible)
        def defined(r): return r is not None and int(r["trades_A"]) >= 20 and int(r["days_A"]) >= 10
        elig = [i for i, x in enumerate(num_vals) if x[2] is not None and x[2]["eligible"] == "1"]
        zones = []
        if elig:
            cur = [elig[0]]
            for i in elig[1:]:
                if i == cur[-1] + 1: cur.append(i)
                else: zones.append(cur); cur = [i]
            zones.append(cur)
        else:
            cand = [i for i, x in enumerate(num_vals) if defined(x[2])]
            cand.sort(key=lambda i: (float(num_vals[i][2]["p"]), -float(num_vals[i][2]["dSR_B1"]), -int(num_vals[i][2]["days_A"])))
            zones = [[cand[0]]] if cand else []
        zp = lambda z: min(float(num_vals[i][2]["p"]) for i in z)
        zones = sorted(zones, key=zp)[:3]
        pts = {}
        for z in zones:
            lo, hi = max(min(z) - 1, 0), min(max(z) + 1, len(num_vals) - 1)
            for i in range(lo, hi):
                for v in interp(num_vals[i][0], num_vals[i + 1][0], ax):
                    pts[fmt(ax, v)] = abs(v - num_vals[z[0]][0])
        old = {x[1] for x in vals}
        new = sorted((k for k in pts if k not in old), key=lambda k: pts[k])
        cut = max(len(new) - 12, 0) if ax != "6a" else max(len(new) - 12, 0)
        keep = new[:12]
        summ[ax] = {"зоны": [[num_vals[i][1] for i in z] for z in zones], "новых": len(keep), "срезано_потолком": cut, "точки": keep}
        for k in keep:
            out.append({"hyp": "П18-" + ax, "axes": json.dumps({"ax": ax, KEY[ax]: k}, ensure_ascii=False)})
    # 6в: 2D (S × usd_min)
    rs = [r for r in rows if json.loads(r["axes"])["ax"] == "6v"]
    cell = {}
    for r in rs:
        s, u = json.loads(r["axes"])["flow+usd_min"].split("|")
        cell[(s, u)] = r
    S = sorted({s for s, _ in cell}, key=float); U = [u for u in ["none"] + P.USD6]
    el = [(s, u) for (s, u), r in cell.items() if r["eligible"] == "1"]
    zone = el
    ss = sorted({S.index(s) for s, _ in zone}); us = sorted({U.index(u) for _, u in zone})
    s_lo, s_hi = max(ss[0] - 1, 0), min(ss[-1] + 1, len(S) - 1); u_lo, u_hi = max(us[0] - 1, 1), min(us[-1] + 1, len(U) - 1)
    newS = [] ; newU = []
    for i in range(s_lo, s_hi):
        newS += ["%g" % round(v, 1) for v in interp(float(S[i]), float(S[i + 1]), "6v")]
    for i in range(u_lo, u_hi):
        newU += ["%d" % (round(v / 500) * 500) for v in interp(float(U[i]), float(U[i + 1]), "6v")]
    Sw = [S[i] for i in range(s_lo, s_hi + 1)] + newS; Uw = [U[i] for i in range(u_lo, u_hi + 1)] + newU
    grid = [(s, u) for s in Sw for u in Uw if (s, u) not in cell]
    summ["6v"] = {"зона": zone, "S_нов": newS, "U_нов": newU, "клеток_нов": len(grid), "потолок_100": len(grid) > 100}
    for s, u in grid:
        out.append({"hyp": "П18-6v", "axes": json.dumps({"ax": "6v", "flow+usd_min": f"{s}|{u}"}, ensure_ascii=False)})
    print(json.dumps(summ, ensure_ascii=False, indent=1))
    tot = {}
    for x in out: tot[x["hyp"]] = tot.get(x["hyp"], 0) + 1
    print("итого клеток F2:", sum(tot.values()), tot, "хеш-мес. ×9 =", sum(tot.values()) * 9)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            w = csv.DictWriter(f, fieldnames=["hyp", "axes"]); w.writeheader(); w.writerows(out)


if __name__ == "__main__":
    main()
