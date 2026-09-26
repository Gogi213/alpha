#!/usr/bin/env python3
"""П-05 (T-29): чтение партии сетки фильтра стены — по месяцам отдельно (В-115), против главного варианта (В-104).

Вход — `portfolio-sim.py --json` одного прогона, где варианты = наборы партии (имя варианта = имя набора) и главный
(`--main`, по умолчанию «главный»). Берётся строка счёта без защит (max_pos 0, kill 0, исключений нет).
На набор и месяц: $, сделок, просадка %, доля прибыльных; Δ$ к главному по суткам с 95 % интервалом блочного
бутстрепа (блок ⌈n^{1/3}⌉, П-02 R2 п.2); R = прибыль / просадка $; «равный риск» (§3а) — множитель k = просадка главного
/ просадка набора, деньги ×k и Δ ряда (набор ×k − главный) с интервалом. Линейно, без пересчёта очереди.

    python tools/compute/p05-read.py --json data/p05/protection-p05-a1.json --out docs/findings/p05-a1-2026-09-27.md
"""
from __future__ import annotations

import argparse
import json
import math
import random

MONTHS = ("август", "сентябрь")


def boot(x, rng, reps=20000):
    n = len(x)
    if n == 0:
        return (0.0, 0.0)
    L = max(1, math.ceil(n ** (1 / 3)))
    out = []
    for _ in range(reps):
        s = []
        while len(s) < n:
            i = rng.randrange(n - L + 1)
            s += x[i:i + L]
        out.append(sum(s[:n]))
    out.sort()
    return out[int(0.025 * reps)], out[int(0.975 * reps)]


def money(v):
    return ("+" if v > 0 else "−" if v < 0 else "") + f"${abs(v):,.0f}".replace(",", " ")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True)
    ap.add_argument("--main", default="главный")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="П-05")
    a = ap.parse_args()
    p = json.load(open(a.json, encoding="utf-8"))
    dep = p.get("deposit_usd") or 2500.0
    G = {(g["variant"], g["epoch"]): g for g in p["grid"]
         if g["max_pos"] == 0 and g["kill"] == 0 and g["exclude"] == "нет" and g["day_stop"] == 0
         and g.get("streak_stop", 0) == 0}
    variants = [v for v in dict.fromkeys(g[0] for g in G) if v != a.main]
    rng = random.Random(7)
    rows, res = [], {}
    for v in [a.main] + variants:
        res[v] = {}
        for m in MONTHS:
            g, b = G.get((v, m)), G.get((a.main, m))
            if not g or not b:
                continue
            days = sorted(set(g["daily"]) | set(b["daily"]))
            d = [g["daily"].get(k, 0) - b["daily"].get(k, 0) for k in days]
            lo, hi = boot(d, rng) if v != a.main else (0.0, 0.0)
            k = (b["dd_pct"] / g["dd_pct"]) if g["dd_pct"] > 0 else None
            if k is not None and v != a.main:
                dk = [g["daily"].get(t, 0) * k - b["daily"].get(t, 0) for t in days]
                klo, khi = boot(dk, rng)
                ksum = sum(dk)
            else:
                klo = khi = ksum = None
            r = {"n": g["n"], "usd": round(g["total_usd"], 2), "dd_pct": round(g["dd_pct"], 3),
                 "win": round(g["win"], 3), "delta": round(sum(d), 2), "delta_ci": [round(lo, 1), round(hi, 1)],
                 "R": round(g["total_usd"] / (g["dd_pct"] / 100 * dep), 2) if g["dd_pct"] > 0 else None,
                 "k": round(k, 2) if k else None, "pos_usd_eq": round(500 * k) if k else None,
                 "usd_eq": round(g["total_usd"] * k, 2) if k else None,
                 "delta_eq": round(ksum, 2) if ksum is not None else None,
                 "delta_eq_ci": [round(klo, 1), round(khi, 1)] if klo is not None else None}
            res[v][m] = r
    lines = [f"# {a.title}", "",
             "Счёт $2500, позиция $500, без защит; Δ — к главному по суткам, 95 % блочный бутстреп. «Равный риск» — "
             "позиция, при которой просадка = главной в том же месяце (линейно, без пересчёта очереди). Все клетки — "
             "на днях подбора; вердикт — только по протоколу после «принято» Судьи.", ""]
    for m in MONTHS:
        lines += [f"## {m.capitalize()}", "",
                  "| набор | $ | сделок | просадка | в плюс | Δ к главному [95 %] | прибыль/просадка | позиция при равной просадке | $ при ней | Δ при равной просадке [95 %] |",
                  "|---|---:|---:|---:|---:|---|---:|---:|---:|---|"]
        for v in [a.main] + variants:
            r = res[v].get(m)
            if not r:
                lines.append(f"| {v} | — | | | | | | | | |")
                continue
            dci = "" if v == a.main else f"{money(r['delta'])} [{money(r['delta_ci'][0])}; {money(r['delta_ci'][1])}]"
            eq = "" if v == a.main or r["delta_eq"] is None else \
                f"{money(r['delta_eq'])} [{money(r['delta_eq_ci'][0])}; {money(r['delta_eq_ci'][1])}]"
            lines.append(f"| {v} | {money(r['usd'])} | {r['n']} | −{r['dd_pct']:.2f} % | {r['win'] * 100:.0f} % | {dci} | "
                         f"{r['R'] if r['R'] is not None else '—'} | {('$' + str(r['pos_usd_eq'])) if r['pos_usd_eq'] else '—'} | "
                         f"{money(r['usd_eq']) if r['usd_eq'] is not None else '—'} | {eq} |")
        lines.append("")
    open(a.out, "w", encoding="utf-8", newline="").write("\n".join(lines))
    json.dump(res, open(a.out.rsplit(".", 1)[0] + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
