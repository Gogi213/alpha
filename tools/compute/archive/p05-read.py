#!/usr/bin/env python3
"""П-05 (T-29): чтение партии сетки фильтра стены — по месяцам отдельно (В-115), против главного варианта (В-104).

Вход — `portfolio-sim.py --json` одного прогона, где варианты = наборы партии (имя варианта = имя набора) и главный
(`--main`, по умолчанию «главный»). Берётся строка счёта без защит (max_pos 0, kill 0, исключений нет).
На набор и месяц: $, сделок, просадка %, доля прибыльных; Δ$ к главному по суткам с 95 % интервалом блочного
бутстрепа (блок ⌈n^{1/3}⌉, П-02 R2 п.2); R = прибыль / просадка $. Линейно, без пересчёта очереди.

«Равный риск» (§3а, поправка Судьи 27.09 `0393f07` п.3, П-05 §12 п.3): множитель k = σ суточного результата
главного / σ суточного результата клетки, потолок k ≤ 3, **k подбирается на одном месяце и применяется к другому**
(перекрёстно: k августа считается по σ суток августа — и используется для чтения сентября, и наоборот). Деньги при
равном риске = сумма (клетка·k) по суткам, Δ ряда = (клетка·k − главный) с тем же блочным бутстрепом. Рядом —
медиана просадки по блочному бутстрепу суток для главного и для клетки·k при этом k (реп. 3000, депозит из JSON).

Охват (§6, §12 п.5): < 10 сделок в месяце → «сделок нет» (без $, Δ, k, позиции — это шум, не число); 10–29 → числа
показаны, но помечены «описание» (в подбор/Холм не идут — правило §12 п.5, ≥ 30 — без изменений).

    python tools/compute/p05-read.py --json data/p05/protection-p05-a1.json --out docs/findings/p05-part1-2026-09-27.md \
        --title "П-05 часть 1: возраст и размер отдельно"
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st

MONTHS = ("август", "сентябрь")
OTHER_MONTH = {"август": "сентябрь", "сентябрь": "август"}
K_CAP = 3.0
COV_NONE, COV_DESC, COV_OK = "none", "desc", "ok"


def coverage(n):
    if n < 10:
        return COV_NONE
    if n < 30:
        return COV_DESC
    return COV_OK


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


def dd_median_boot(x, rng, deposit, reps=3000):
    """Медиана максимальной просадки (% от депозита) по блочному бутстрепу суточного ряда x (в хронологическом
    порядке, как в §12 п.3: «медиана просадки по блочному бутстрепу суток для обоих рядов при этом k»)."""
    n = len(x)
    if n == 0 or deposit <= 0:
        return None
    L = max(1, math.ceil(n ** (1 / 3)))
    dds = []
    for _ in range(reps):
        s = []
        while len(s) < n:
            i = rng.randrange(n - L + 1)
            s += x[i:i + L]
        s = s[:n]
        eq = deposit
        peak = deposit
        dd = 0.0
        for v in s:
            eq += v
            if eq > peak:
                peak = eq
            elif peak > 0:
                dd = max(dd, (peak - eq) / peak * 100)
        dds.append(dd)
    dds.sort()
    return dds[len(dds) // 2]


def money(v):
    return ("+" if v > 0 else "−" if v < 0 else "") + f"${abs(v):,.0f}".replace(",", " ")


def sigma_over_days(daily, days):
    return st.pstdev([daily.get(d, 0.0) for d in days])


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
    res = {}

    # σ по месяцу (для равного риска, перекрёстно) — считается один раз для всех клеток и месяцев.
    sigma = {}  # (variant, month) -> (sigma_main, sigma_cell, days_этого_месяца)
    for v in [a.main] + variants:
        for m in MONTHS:
            g, b = G.get((v, m)), G.get((a.main, m))
            if not g or not b:
                continue
            days = sorted(set(g["daily"]) | set(b["daily"]))
            sigma[(v, m)] = (sigma_over_days(b["daily"], days), sigma_over_days(g["daily"], days), days)

    for v in [a.main] + variants:
        res[v] = {}
        for m in MONTHS:
            g, b = G.get((v, m)), G.get((a.main, m))
            if not g or not b:
                continue
            n = g["n"]
            cov = coverage(n) if v != a.main else COV_OK
            days = sorted(set(g["daily"]) | set(b["daily"]))
            d = [g["daily"].get(t, 0) - b["daily"].get(t, 0) for t in days]
            lo, hi = boot(d, rng) if v != a.main else (0.0, 0.0)

            # k — перекрёстно: подбор на ДРУГОМ месяце (σ там), применяется к чтению этого месяца.
            k = None
            if v != a.main and cov != COV_NONE:
                fit_m = OTHER_MONTH[m]
                fit = sigma.get((v, fit_m))
                if fit and fit[1] > 0:
                    k = min(K_CAP, fit[0] / fit[1])
            dk = klo = khi = ksum = None
            dd_main_med = dd_cell_med = None
            if k is not None:
                main_series = [b["daily"].get(t, 0) for t in days]
                cell_series_k = [g["daily"].get(t, 0) * k for t in days]
                dk = [c - mm for c, mm in zip(cell_series_k, main_series)]
                klo, khi = boot(dk, rng)
                ksum = sum(dk)
                dd_main_med = dd_median_boot(main_series, rng, dep)
                dd_cell_med = dd_median_boot(cell_series_k, rng, dep)

            r = {"n": n, "usd": round(g["total_usd"], 2), "dd_pct": round(g["dd_pct"], 3),
                 "win": round(g["win"], 3), "delta": round(sum(d), 2), "delta_ci": [round(lo, 1), round(hi, 1)],
                 "R": round(g["total_usd"] / (g["dd_pct"] / 100 * dep), 2) if g["dd_pct"] > 0 else None,
                 "cov": cov,
                 "k": round(k, 2) if k is not None else None, "pos_usd_eq": round(500 * k) if k else None,
                 "usd_eq": round(g["total_usd"] * k, 2) if k else None,
                 "delta_eq": round(ksum, 2) if ksum is not None else None,
                 "delta_eq_ci": [round(klo, 1), round(khi, 1)] if klo is not None else None,
                 "dd_boot_main_med": round(dd_main_med, 2) if dd_main_med is not None else None,
                 "dd_boot_cell_med": round(dd_cell_med, 2) if dd_cell_med is not None else None}
            res[v][m] = r

    lines = [f"# {a.title}", "",
             "Счёт $2500, позиция $500, без защит; Δ — к главному по суткам, 95 % блочный бутстреп. «Равный риск» "
             "(§3а) — k = σ суточного результата главного / σ клетки, потолок k ≤ 3, **подбирается на другом месяце "
             "и применяется здесь** (перекрёстно, П-05 §12 п.3); позиция при равном риске = $500·k; рядом — медиана "
             "просадки по блочному бутстрепу суток для главного и для клетки·k при этом же k. Линейно, без "
             "пересчёта очереди. Охват (§12 п.5): **< 10 сделок в месяце — «сделок нет»**, без $/Δ/k/позиции; "
             "**10–29 — числа показаны, но это описание**, не в подбор и не в Холм; ≥ 30 — обычное чтение. Все "
             "клетки — на днях подбора; вердикт — только по протоколу после «принято» Судьи.", ""]
    for m in MONTHS:
        lines += [f"## {m.capitalize()}", "",
                  "| набор | охват | $ | сделок | просадка | в плюс | Δ к главному [95 %] | прибыль/просадка | "
                  "k (перекрёстно) | позиция при равном риске | $ при ней | Δ при равном риске [95 %] | "
                  "просадка-медиана (бутстреп) главный / клетка·k |",
                  "|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|"]
        for v in [a.main] + variants:
            r = res[v].get(m)
            if not r:
                lines.append(f"| {v} | — | | | | | | | | | | | |")
                continue
            if v != a.main and r["cov"] == COV_NONE:
                lines.append(f"| {v} | сделок нет (n={r['n']}) | — | {r['n']} | — | — | сделок нет | — | — | — | — | — | — |")
                continue
            cov_tag = "" if v == a.main else ("описание" if r["cov"] == COV_DESC else "≥ 30")
            dci = "" if v == a.main else f"{money(r['delta'])} [{money(r['delta_ci'][0])}; {money(r['delta_ci'][1])}]"
            eq = "" if v == a.main or r["delta_eq"] is None else \
                f"{money(r['delta_eq'])} [{money(r['delta_eq_ci'][0])}; {money(r['delta_eq_ci'][1])}]"
            ddb = "" if v == a.main or r["dd_boot_main_med"] is None else \
                f"−{r['dd_boot_main_med']:.2f} % / −{r['dd_boot_cell_med']:.2f} %"
            lines.append(
                f"| {v} | {cov_tag} | {money(r['usd'])} | {r['n']} | −{r['dd_pct']:.2f} % | {r['win'] * 100:.0f} % | "
                f"{dci} | {r['R'] if r['R'] is not None else '—'} | "
                f"{('×' + str(r['k'])) if r['k'] is not None else '—'} | "
                f"{('$' + str(r['pos_usd_eq'])) if r['pos_usd_eq'] else '—'} | "
                f"{money(r['usd_eq']) if r['usd_eq'] is not None else '—'} | {eq} | {ddb} |")
        lines.append("")
    open(a.out, "w", encoding="utf-8", newline="").write("\n".join(lines))
    json.dump(res, open(a.out.rsplit(".", 1)[0] + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(a.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
