#!/usr/bin/env python3
"""TK-289 П-18 шаг 4: центры плато → стопка (≤ 2 клетки) и F3 (шаг F2/4, ≤ 8 новых точек на ось, 2D ≤ 100 клеток).
Вход: CSV сита по всем 9 осям (docs/findings/tk289-sieve-all9-2026-10-21.csv, столбец plateau=1 — плато F1∪F2).
Правила (П-18 §4 п. 4, решение Судьи 21.10 reviews/tk289-f2-6b8-before-stack-2026-10-21.md; записаны до счёта):
 · стопка А — центр = клетка плато с наибольшей ΔSR_норм на А (ничья — меньше p); стопка Б — центр по ширине плато:
   1D — средняя по рангу клетка плато (ничья — меньше p), 2D — клетка плато с наибольшим числом годных из 8 соседей, ничья —
   ближе к центру масс плато (по рангам), затем меньше p (P-14 review 11.10);
 · U (usd_min) в клетке один: А — из 6а/6в у которой выше ΔSR центра А; Б — у которой шире плато (клеток plateau=1);
 · F3: окрестность = плато + по одному соседу с каждой стороны, 3 внутренние точки между соседями (шаг F2/4), ближе к центру А.
Запуск: python tools/compute/tk289-stack-f3.py <sieve-all9.csv> [--out f3-grid.csv] [--stack-out stack.json]"""
import argparse, csv, json, math

UN = ["none", 10000, 14000, 17000, 20000, 22000, 24500, 27000, 30000, 34000, 38500, 44000, 50000, 75000, 100000, 150000, 200000, 300000]


def pts(a, b):
    return [a + (b - a) * k / 4 for k in (1, 2, 3)]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("sieve"); ap.add_argument("--out"); ap.add_argument("--stack-out")
    a = ap.parse_args()
    rows = list(csv.DictReader(open(a.sieve, encoding="utf-8")))
    ok = lambda r: r["eligible"] == "1"
    pl = lambda r: r["plateau"] == "1"
    f = lambda r: (float(r["p"]), -float(r["dSR_B1"]))
    one = {}
    for ax, key in (("5", "ttl"), ("6a", "usd_min")):
        rs = sorted(((float(json.loads(r["axes"])[key]), r) for r in rows if r["hyp"] == "П18-" + ax), key=lambda t: t[0])
        P = [(v, r) for v, r in rs if pl(r)]
        A = max(P, key=lambda t: (float(t[1]["dSR_B1"]), -float(t[1]["p"])))
        mid = sorted(P, key=lambda t: t[0])
        n = len(mid); cand = [mid[(n - 1) // 2], mid[n // 2]]
        B = min(cand, key=lambda t: float(t[1]["p"]))
        one[ax] = dict(rs=rs, P=P, A=A, B=B)
    # 6в: 2D S × U
    cell = {}
    for r in rows:
        if r["hyp"] != "П18-6v":
            continue
        s, u = json.loads(r["axes"])["flow+usd_min"].split("|")
        cell[(float(s), "none" if u == "none" else int(u))] = r
    S6 = sorted({s for s, _ in cell}); U6 = [u for u in UN if any(uu == u for _, uu in cell)]
    rk = lambda s, u: (S6.index(s), U6.index(u))
    P6 = [k for k, r in cell.items() if pl(r)]
    cm = (sum(rk(*k)[0] for k in P6) / len(P6), sum(rk(*k)[1] for k in P6) / len(P6))
    def nb(k):
        i, j = rk(*k)
        return sum(1 for di in (-1, 0, 1) for dj in (-1, 0, 1) if (di or dj) and 0 <= i + di < len(S6) and 0 <= j + dj < len(U6)
                   and (S6[i + di], U6[j + dj]) in cell and ok(cell[(S6[i + di], U6[j + dj])]))
    A6 = max(P6, key=lambda k: (float(cell[k]["dSR_B1"]), -float(cell[k]["p"])))
    B6 = min(P6, key=lambda k: (-nb(k), math.hypot(rk(*k)[0] - cm[0], rk(*k)[1] - cm[1]), float(cell[k]["p"])))
    # какой U: А — выше ΔSR центра, Б — шире плато
    dA6a = float(one["6a"]["A"][1]["dSR_B1"]); dA6v = float(cell[A6]["dSR_B1"])
    uA = "6в" if dA6v >= dA6a else "6а"
    uB = "6в" if len(P6) >= len(one["6a"]["P"]) else "6а"
    def pair(which):
        if which == "6в":
            return (A6 if cur == "A" else B6)
        return (None, int(one["6a"][cur][0]))
    stacks = []
    for cur, uw in (("A", uA), ("B", uB)):
        ttl = int(one["5"][cur][0])
        if uw == "6в":
            s, u = (A6 if cur == "A" else B6)
            st = dict(stack=cur, ttl=ttl, flow=s, usd_min=u, U_axis="6в", dSR_центра=float(cell[(s, u)]["dSR_B1"]))
        else:
            st = dict(stack=cur, ttl=ttl, flow=None, usd_min=int(one["6a"][cur][0]), U_axis="6а", dSR_центра=float(one["6a"][cur][1]["dSR_B1"]))
        stacks.append(st)
    if stacks[0]["ttl"] == stacks[1]["ttl"] and stacks[0]["flow"] == stacks[1]["flow"] and stacks[0]["usd_min"] == stacks[1]["usd_min"]:
        stacks = stacks[:1]
    # F3 1D: ttl (целые с), 6а (100 $); ≤ 8 новых ближе к центру А
    out = []; info = {}
    for ax, key, nd in (("5", "ttl", 0), ("6a", "usd_min", -2)):
        rs = one[ax]["rs"]; vals = [v for v, _ in rs] + ([1800.0] if ax == "5" else [])
        vals = sorted(vals); Pv = [v for v, _ in one[ax]["P"]]; ca = one[ax]["A"][0]
        lo = max(vals.index(min(Pv)) - 1, 0); hi = min(vals.index(max(Pv)) + 1, len(vals) - 1)
        new = {}
        for i in range(lo, hi):
            for v in pts(vals[i], vals[i + 1]):
                v = int(round(v, nd)) if nd == 0 else int(round(v / 100) * 100)
                if v not in vals:
                    new[v] = abs(math.log(v / ca))
        keep = sorted(new, key=lambda v: new[v])[:8]
        info[ax] = dict(центр_А=ca, окно=[vals[lo], vals[hi]], новых=len(keep), точки=sorted(keep), срезано=max(len(new) - 8, 0))
        for v in sorted(keep):
            out.append(dict(hyp="П18-" + ax, axes=json.dumps({"ax": ax, key: str(v)}, ensure_ascii=False)))
    # F3 2D: 6в вокруг центра А: ±1 ранг по S и по U (соседи плато), 3 внутренние точки между рангами
    i0, j0 = rk(*A6)
    Sw = S6[max(i0 - 1, 0):i0 + 2]; Uw = [u for u in U6[max(j0 - 1, 1):j0 + 2]]
    newS = sorted({round(v, 2) for x, y in zip(Sw, Sw[1:]) for v in pts(x, y)}); newU = sorted({int(round(v / 100) * 100) for x, y in zip(Uw, Uw[1:]) for v in pts(x, y)})
    SS = sorted(set(Sw) | set(newS)); UU = sorted(set(Uw) | set(newU))
    grid = [(s, u) for s in SS for u in UU if (s, u) not in cell and s not in ()]
    info["6v"] = dict(центр_А=A6, S_окно=Sw, U_окно=Uw, S_нов=newS, U_нов=newU, клеток=len(grid), потолок_100=len(grid) > 100)
    for s, u in grid:
        out.append(dict(hyp="П18-6v", axes=json.dumps({"ax": "6v", "flow+usd_min": f"{s:g}|{u}"}, ensure_ascii=False)))
    for st in stacks:
        out.append(dict(hyp="П18-стопка" + st["stack"], axes=json.dumps({"ax": "stack", **{k: st[k] for k in ("ttl", "flow", "usd_min")}}, ensure_ascii=False)))
    print(json.dumps(dict(стопки=stacks, центры=dict(A6в=A6, B6в=B6, плато6в=len(P6), плато6а=len(one["6a"]["P"]), плато5=len(one["5"]["P"]), dSR_А_6а=dA6a, dSR_А_6в=dA6v), F3=info), ensure_ascii=False, indent=1, default=str))
    tot = {}
    for x in out: tot[x["hyp"]] = tot.get(x["hyp"], 0) + 1
    print("итого клеток:", sum(tot.values()), tot, "хеш-мес. ×9 =", sum(tot.values()) * 9)
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            w = csv.DictWriter(fh, fieldnames=["hyp", "axes"]); w.writeheader(); w.writerows(out)


if __name__ == "__main__":
    main()
