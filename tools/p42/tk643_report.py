#!/usr/bin/env python3
"""П-42 §13.43 (TK-643): отчёт по результатам юнита tk643-pnl.
  python3 tk643_report.py <каталог res (cells43-*.json, verdict43.json, cross43-*.json)> <каталог вывода>
Пишет <вывод>/p42-stops-exits-775-2026-11-20.md и <вывод>/p42-stops-exits-775-2026-11-20/cells-all.md. Доллары — ставка 500 $; Sharpe — годовой; без бп, без медиан."""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk643_run as t  # noqa: E402

RES, OUT = sys.argv[1:3]
NAME = "p42-stops-exits-775-2026-11-20"
SIDE = {"L": "лонг", "S": "шорт"}
MONTHS = ("июль", "август", "сентябрь", "октябрь", "01–13.11")
FORM_TXT = {"none": "только `s`", "ls0.5": "`ls=0.5`", "ls1": "`ls=1`", "c3": "`sn=3`", "c5": "`sn=5`", "lin": "`sn=3` лин.", "geo": "`sn=3` геом. + `bm=1`", "step": "`sn=3` шаг + `bm=1`"}


def f(x, d=2, sign=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    s = ("%.*f" % (d, abs(x))).replace(".", ",")
    neg = x < 0 and float(s.replace(",", ".")) != 0
    return ("−" if neg else ("+" if sign and float(s.replace(",", ".")) != 0 else "")) + s


def stop_txt(r):
    s = r["rr"]["stops"]
    return "%d · %s $ (%s %%)" % (s["n"], f(s["mean_loss"], 1), f(s["mean_loss_pct"], 1)) if s["n"] else "нет"


HDR = "| клетка | сделок | Σ $ | $/сделку | Sharpe | Δ $/сделку [нижн. 5 %] | p | ΔΣ $ | " + " | ".join(MONTHS) + " | R:R | стопов · убыток (% маржи) | просадка $ |"
SEP = "|" + "---|" * (HDR.count("|") - 1)


def row(name, r, base=False):
    d = r.get("delta")
    dd = "—" if base else "%s [%s]" % (f(d["d"], 3, True), f(d["lower"], 3, True))
    return "| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        name, r["n"], f(r["sum"], 1, True), f(r["mean"], 3, True), f(r["sharpe"], 2, True), dd, "—" if base else "%.4f" % d["p"], "—" if base else f(d["dsum"], 1, True),
        " | ".join(f(m[1], 0, True) for m in r["months"]), f(r["rr"]["rr"]), stop_txt(r), f(r["maxdd"], 1))


def main():
    os.makedirs(os.path.join(OUT, NAME), exist_ok=True)
    V = json.load(open(os.path.join(RES, "verdict43.json"), encoding="utf-8"))
    C = {s: json.load(open(os.path.join(RES, "cells43-%s.json" % s), encoding="utf-8")) for s in t.SIDES}
    D = {s: json.load(open(os.path.join(RES, "cross43-%s.json" % s), encoding="utf-8")) for s in t.SIDES if os.path.exists(os.path.join(RES, "cross43-%s.json" % s))}
    L = ["# П-42: стоп и выходы целиком на честной вселенной h775 — совместные сетки (TK-643, §13.43, 20.11.2026)", ""]
    for s in t.SIDES:
        b = C[s]["base"]
        st = b["rr"]["stops"]
        L.append("**%s, база h775:** %d сделок, Σ %s $, %s $ на сделку, Sharpe %s; **R:R %s, стопов %d, убыток стопа в среднем %s $ = %s %% маржи при плече 2** (макс. %s $ = %s %%)." % (
            SIDE[s], b["n"], f(b["sum"], 1, True), f(b["mean"], 3, True), f(b["sharpe"], 2), f(b["rr"]["rr"]), st["n"], f(st["mean_loss"], 2), f(st["mean_loss_pct"], 2), f(st["max_loss"], 2), f(st["max_loss_pct"], 2)))
    L += ["", "Окно 01.07–13.11 (136 сут.), 775 контрактов, ставка 500 $, комиссии Bybit по тарифу владельца; всё внутри окна — **«найдено на этих данных»**; протокол `docs/research/P-42-level-breakout.md` §13.43 (критик `thr_sia32k2tr5` PASS на `3ef80aeeb`).", "", "## Ответ", ""]
    for s in t.SIDES:
        v = V[s]
        L.append("- **%s:** %s. Испытаний m = %d (A %d + B %d + C %d), шумовая планка по N = %d." % (SIDE[s], v["answer"], v["m"], 63, 34, 215, v["trials_noise"]))
        cnt = {g: sum(1 for c in v["cells"].values() if c["gates"][g]) for g in "abcdefg"}
        L.append("  Прошли ворота по отдельности: (а) минимум сделок %d, (б) Холм %d, (в) ≥ 4 из 5 месяцев %d, (г) без 2 суток / 2 монет %d, (д) охрана суммы %d, (е) плато %d, (ж) выше шума %d." % tuple(cnt[g] for g in "abcdefg"))
        for cid in v["passed"]:
            L.append("  - `%s`: оси плато проверены — %s." % (cid, ", ".join("%s (%s)" % (k, ", ".join("`%s`" % x for x in ids)) for k, ids in v["cells"][cid]["neighbours"].items())))
    L.append("")
    for s in t.SIDES:
        cells, v = C[s]["cells"], V[s]
        L += ["## %s" % SIDE[s].capitalize(), ""]
        top = sorted(cells, key=lambda c: -cells[c]["delta"]["d"])[:10]
        L += ["### Десять клеток с наибольшей Δ $/сделку (описательно, «найдено на этих данных»; не «лучшие»: отбор из %d клеток)" % v["m"], "", HDR + " ворота (а…ж) |", SEP + "---|", row("база", C[s]["base"], base=True) + " — |"]
        for c in top:
            g = v["cells"][c]["gates"]
            L.append(row("`%s`" % c, cells[c]) + " %s |" % "".join(k if g[k] else "·" for k in "abcdefg"))
        L.append("")
        # блок A: матрица s × форма (Δ $/сделку)
        L += ["### Блок A — стоп: Δ $/сделку к базе (строки — форма, столбцы — `s`, %)", "", "| форма | " + " | ".join(t.fmt(x) for x in t.S_VALS) + " |", "|---|" + "---|" * len(t.S_VALS)]
        for fn, _ in t.FORMS:
            L.append("| %s | %s |" % (FORM_TXT[fn], " | ".join("0 (база)" if (fn == "none" and x == 5) else f(cells["A_s%g_%s" % (x, fn)]["delta"]["d"], 3, True) for x in t.S_VALS)))
        L += ["", "Стопов и средний убыток стопа (% маржи):", "", "| форма | " + " | ".join(t.fmt(x) for x in t.S_VALS) + " |", "|---|" + "---|" * len(t.S_VALS)]
        for fn, _ in t.FORMS:
            L.append("| %s | %s |" % (FORM_TXT[fn], " | ".join("база" if (fn == "none" and x == 5) else stop_txt(cells["A_s%g_%s" % (x, fn)]) for x in t.S_VALS)))
        L += ["", "### Блок B — выходы после касания: Δ $/сделку (строки — `rt`, столбцы — `tx`)", "", "| `rt` | " + " | ".join(n for n, _ in t.TX) + " |", "|---|" + "---|" * len(t.TX)]
        for rn, _ in t.RT:
            L.append("| %s | %s |" % (rn, " | ".join("0 (база)" if ("B_%s_%s" % (rn, tn)) not in cells else f(cells["B_%s_%s" % (rn, tn)]["delta"]["d"], 3, True) for tn, _ in t.TX)))
        L += ["", "### Блок C — тейки / безубыток / трейл: средняя Δ $/сделку по значению оси (среднее по остальным осям; описательно)", ""]
        for k, vals in t.C_AXES:
            parts = []
            for x in vals:
                ds = [r["delta"]["d"] for c, r in cells.items() if r["block"] == "C" and r["ax"][k] == x]
                parts.append("%s = %s: %s (клеток %d, Δ > 0 у %d)" % (k, t.fmt(x), f(sum(ds) / len(ds), 3, True), len(ds), sum(1 for d in ds if d > 0)))
            L.append("- " + "; ".join(parts))
        risk = [c for c, r in cells.items() if r["delta"]["dsum"] >= -r["delta"]["se_sum"] and (
            (r["rr"]["stops"]["n"] and C[s]["base"]["rr"]["stops"]["n"] and r["rr"]["stops"]["mean_loss_pct"] >= 0.75 * C[s]["base"]["rr"]["stops"]["mean_loss_pct"]) or r["maxdd"] <= 0.75 * C[s]["base"]["maxdd"])]
        L += ["", "### Срез «риск» (описательно): убыток стопа или просадка ниже базы на ≥ 25 %% при ΔΣ ≥ −1·SE — %d клеток" % len(risk), ""]
        if risk:
            L += [HDR, SEP] + [row("`%s`" % c, cells[c]) for c in sorted(risk, key=lambda c: -cells[c]["delta"]["dsum"])[:15]]
            if len(risk) > 15:
                L.append("(ещё %d — в `cells-all.md`)" % (len(risk) - 15))
        if s in D:
            L += ["", "### Блок D — сочетания двух лучших клеток A, B, C (описательно: **выбрано по P&L тех же 136 суток, 0 испытаний, вне Холма**)", "", HDR, SEP]
            L += [row("`%s`" % c, r) for c, r in sorted(D[s]["cells"].items(), key=lambda kv: -kv[1]["delta"]["d"])]
        L += ["", "Ворота: G-base %s; G-dup групп %d (одинаковые сделки, в Холме оставлены); сделок «конец данных» у базы %d." % ("ok" if C[s]["gbase"] else "ОШИБКА", len(C[s]["gdup"]), C[s]["gwin_forced_base"]), ""]
    open(os.path.join(OUT, NAME + ".md"), "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")
    A = ["# TK-643: все клетки (§13.43)", ""]
    for s in t.SIDES:
        A += ["## %s" % SIDE[s], "", HDR + " ворота |", SEP + "---|", row("база", C[s]["base"], base=True) + " — |"]
        for c, r in C[s]["cells"].items():
            g = V[s]["cells"][c]["gates"]
            A.append(row("`%s`" % c, r) + " %s |" % "".join(k if g[k] else "·" for k in "abcdefg"))
        A.append("")
    open(os.path.join(OUT, NAME, "cells-all.md"), "w", encoding="utf-8", newline="\n").write("\n".join(A) + "\n")


if __name__ == "__main__":
    main()
