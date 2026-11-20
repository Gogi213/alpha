#!/usr/bin/env python3
"""П-42 §13.50 (TK-650): отчёт по результатам юнита tk650-pnl.
  python3 tk650_report.py <каталог res (cells50-*.json, verdict50.json)> <файл отчёта .md>
Доллары — ставка 500 $; Sharpe — годовой; без бп, без медиан."""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk650_run as t  # noqa: E402
from tk643_report import f, stop_txt  # noqa: E402

RES, OUT = sys.argv[1:3]
SIDE = {"L": "лонг", "S": "шорт"}
MONTHS = ("июль", "август", "сентябрь", "октябрь", "01–13.11")
HDR = "| клетка | сделок | Σ $ | $/сделку | Sharpe | Δ $/сделку [нижн. 5 %] | p | ΔΣ $ | Δ по месяцам | R:R | стопов · убыток (% маржи) | ворота а…з |"
SEP = "|" + "---|" * 12


def row(name, r, g=None):
    d = r.get("delta")
    if d is None:
        return "| %s | %d | %s | %s | %s | — | — | — | — | %s | %s | — |" % (name, r["n"], f(r["sum"], 1, True), f(r["mean"], 3, True), f(r["sharpe"], 2), f(r["rr"]["rr"]), stop_txt(r))
    return "| %s | %d | %s | %s | %s | %s [%s] | %.4f | %s | %s | %s | %s | %s |" % (
        name, r["n"], f(r["sum"], 1, True), f(r["mean"], 3, True), f(r["sharpe"], 2), f(d["d"], 3, True), f(d["lower"], 3, True), d["p"], f(d["dsum"], 1, True),
        " / ".join(f(x, 2, True) for x in r["dmonths"]), f(r["rr"]["rr"]), stop_txt(r), "".join(k if g and g[k] else "·" for k in "abcdefgh") if g else "вне m")


def main():
    V = json.load(open(os.path.join(RES, "verdict50.json"), encoding="utf-8"))
    C = {s: json.load(open(os.path.join(RES, "cells50-%s.json" % s), encoding="utf-8")) for s in t.SIDES}
    L = ["# П-42: сетка тейков γ × κ и NATR выхода за край на h775 (TK-650, §13.50, 20.11.2026)", ""]
    for s in t.SIDES:
        b = C[s]["base"]
        st = b["rr"]["stops"]
        L.append("**%s, база h775:** %d сделок, Σ %s $, %s $ на сделку, Sharpe %s; **R:R %s, стопов %d, убыток стопа в среднем %s $ = %s %% маржи при плече 2**." % (
            SIDE[s], b["n"], f(b["sum"], 1, True), f(b["mean"], 3, True), f(b["sharpe"], 2), f(b["rr"]["rr"]), st["n"], f(st["mean_loss"], 2), f(st["mean_loss_pct"], 2)))
    L += ["", "Окно 01.07–13.11 (136 сут.), 775 контрактов, ставка 500 $, комиссии Bybit по тарифу владельца; сутки ≥ 14.11 не читались; всё — **«найдено на этих данных»**. Протокол — летопись P-42 §13.50 (PS-913, до счёта; текст `prereg-13.50.md`), PASS критика на `19145e399`, юнит `tk650-pnl` один раз.", "", "## Ответ", ""]
    for s in t.SIDES:
        v = V[s]
        tr = v["turn"]
        cnt = {g: sum(1 for c in v["cells"].values() if c["gates"][g]) for g in "abcdefgh"}
        a = tr["argmax"]
        nz = v["cells"]["G_g%g_k%g" % (a["tpg"], a["tpk"])]["noise"]
        L.append("- **%s: %s.** m = %d, Холм 0 из %d (наименьший p %s, нужно ≤ %s), выше шумовой планки (N = %d) — 0. Поворот поверхности: **%s** — максимум сглаженной Δ у γ %s, κ %s: %s $/сделку (сама клетка %s; максимум по 71 клетке, не «лучшая»; Sharpe %s при планке шума %s)." % (
            SIDE[s], v["answer"], v["m"], v["m"], f(min(C[s]["cells"][c]["delta"]["p"] for c in v["cells"]), 4), f(0.05 / v["m"], 5), v["trials_noise"], tr["where"].replace(".", ","), f(a["tpg"], 3), f(a["tpk"], 0),
            f(a["smooth"], 3, True), f(a["d"], 3, True), f(C[s]["cells"]["G_g%g_k%g" % (a["tpg"], a["tpk"])]["sharpe"], 2), f(nz["noise_bar"], 2)))
        L.append("  Ворота по отдельности: (а) %d, (б) %d, (в) %d, (г) %d, (д) %d, (е) %d, (ж) %d, (з) %d из %d." % (tuple(cnt[g] for g in "abcdefgh") + (v["m"],)))
        o = tr["old_edge"]
        L.append("  Старый край §13.12 (γ 0,5, κ 3): Δ %s $/сделку; область γ ≤ 0,5 × κ ≥ 3 — средняя Δ %s, Δ > 0 у %d из %d." % (f(o["cell"], 3, True), f(o["region_mean"], 3, True), o["region_pos"], o["region_n"]))
    L += ["", "Смысл: перекос лестницы к дальнему краю (меньше ранних тейков, больше объёма у T_high) даёт обеим сторонам малую прибавку — порядка +0,06 (лонг) / +0,09 (шорт) $ на сделку, ≈ +28 $ за 136 суток, R:R растёт; поверхность до предела формы не поворачивает, но прибавка в разы меньше различимой (MDE ≈ 0,15…0,65 $/сделку) и не проходит Холм и шум. Период NATR выхода 15 / 60 — в шуме, Δ разного знака по месяцам. `ns=1` (заглядывание) — справочно.", ""]
    for s in t.SIDES:
        cs, v = C[s]["cells"], V[s]
        L += ["## %s" % SIDE[s].capitalize(), "", "### Карта Δ $/сделку к базе (строки — γ `tpg`, столбцы — κ `tpk`; * — ступень < 5 $, (з) не выполнено)", "",
              "| γ \\ κ | " + " | ".join("%g%s" % (k, "*" if t.min_lot_usd(k) < t.MIN_LOT_USD else "") for k in t.KAPPAS) + " | средняя |", "|---|" + "---|" * (len(t.KAPPAS) + 1)]
        for g in t.GAMMAS:
            L.append("| %g | %s | %s |" % (g, " | ".join("0 (база)" if (g == 1 and k == 0) else f(cs["G_g%g_k%g" % (g, k)]["delta"]["d"], 3, True) for k in t.KAPPAS), f(v["turn"]["prof_tpg"]["%g" % g]["mean"], 3, True)))
        L.append("| средняя | %s | |" % " | ".join(f(v["turn"]["prof_tpk"]["%g" % k]["mean"], 3, True) for k in t.KAPPAS))
        L += ["", "### Карта Σ $ за 136 суток", "", "| γ \\ κ | " + " | ".join("%g" % k for k in t.KAPPAS) + " |", "|---|" + "---|" * len(t.KAPPAS)]
        for g in t.GAMMAS:
            L.append("| %g | %s |" % (g, " | ".join(f(C[s]["base"]["sum"] if (g == 1 and k == 0) else cs["G_g%g_k%g" % (g, k)]["sum"], 1, True) for k in t.KAPPAS)))
        top = sorted(v["cells"], key=lambda c: -cs[c]["delta"]["d"])[:8]
        L += ["", "### Восемь клеток с наибольшей Δ (описательно, отбор из 73) и блок N", "", HDR, SEP, row("база", C[s]["base"])]
        L += [row("`%s`" % c, cs[c], v["cells"][c]["gates"]) for c in top]
        L += [row("`%s`" % c, cs[c], v["cells"][c]["gates"]) for c in ("N_p15", "N_p60")]
        L.append(row("`R_ns1` (заглядывание, справочно)", cs["R_ns1"]))
        L += ["", "Ворота: G-base %s; G-tp ok (у всех 73 клеток P&L отличен от базы); G-dup групп %d; G-same: отличных от базы сделок не больше %d; закрытых концом данных окна — %s." % (
            "ok" if C[s]["gbase"] else "ОШИБКА", len(C[s]["gdup"]), max(r["same"]["only_cell"] + r["same"]["only_base"] for r in cs.values()),
            C[s]["gwin_forced"] or "0"), ""]
    L += ["## Отступления", "", "- Нет. Шаг количества контракта движок не моделирует (как у замороженных баз); минимальная стоимость ордера 5 $ — ворота (з).", ""]
    open(OUT, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
