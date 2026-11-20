#!/usr/bin/env python3
"""П-42 §13.49 (TK-649): отчёт по результатам юнита tk649-pnl.
  python3 tk649_report.py <каталог res (cells49-*.json, verdict49.json, gwin49.json, gbyte49.json)> <каталог вывода>
Пишет <вывод>/p42-time-no-touch-exit-2026-11-20.md. Доллары — ставка 500 $; Sharpe — годовой; без бп, без медиан."""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tk643_report as r3  # noqa: E402  (f, stop_txt — форматы §13.43)
import tk649_run as t  # noqa: E402

NAME = "p42-time-no-touch-exit-2026-11-20"
SIDE = {"L": "лонг", "S": "шорт"}
f, stop_txt = r3.f, r3.stop_txt


def tu_txt(r):
    k = r["rr"]["kinds"].get("tu")
    return "%d · Σ %s $ · %s $/сд." % (k["n"], f(k["sigma"], 1, True), f(k["mean"], 2, True)) if k else "—"


HDR = ("| клетка | сделок | Σ $ | $/сделку | Sharpe | Δ $/сделку [нижн. 5 %] (SE) | p | ΔΣ $ | " + " | ".join(r3.MONTHS)
       + " | R:R | стопов · убыток (% маржи) | выходов `tu` · Σ · $/сд. | просадка $ | шум: планка · дефл. | ворота (а…ж) |")
SEP = "|" + "---|" * (HDR.count("|") - 1)


def row(name, r, v=None):
    d = r.get("delta")
    if v is None:
        dd, p, ds, nz, g = "—", "—", "—", "—", "—"
    else:
        dd, p, ds = "%s [%s] (%s)" % (f(d["d"], 3, True), f(d["lower"], 3, True), f(d["se"], 3)), "%.4f" % d["p"], f(d["dsum"], 1, True)
        nz = "%s · %s" % (f(v["noise"]["noise_bar"], 2), f(v["noise"]["deflated"], 2))
        g = "".join(k if v["gates"][k] else "·" for k in "abcdefg")
    return "| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        name, r["n"], f(r["sum"], 1, True), f(r["mean"], 3, True), f(r["sharpe"], 2, True), dd, p, ds,
        " | ".join(f(m[1], 0, True) for m in r["months"]), f(r["rr"]["rr"]), stop_txt(r), tu_txt(r), f(r["maxdd"], 1), nz, g)


def main():
    res, out = sys.argv[1:3]
    V = json.load(open(os.path.join(res, "verdict49.json"), encoding="utf-8"))
    C = {s: json.load(open(os.path.join(res, "cells49-%s.json" % s), encoding="utf-8")) for s in t.SIDES}
    G = json.load(open(os.path.join(res, "gwin49.json"), encoding="utf-8"))
    B = json.load(open(os.path.join(res, "gbyte49.json"), encoding="utf-8"))
    L = ["# П-42: выход «уровень не тронут за T минут от входа» (ключ `tu`) на честной вселенной h775 (TK-649, §13.49, 20.11.2026)", ""]
    for s in t.SIDES:
        b = C[s]["base"]
        st = b["rr"]["stops"]
        L.append("**%s, база h775:** %d сделок, Σ %s $, %s $ на сделку, Sharpe %s; **R:R %s, стопов %d, убыток стопа в среднем %s $ = %s %% маржи при плече 2** (макс. %s $ = %s %%)." % (
            SIDE[s], b["n"], f(b["sum"], 1, True), f(b["mean"], 3, True), f(b["sharpe"], 2), f(b["rr"]["rr"]), st["n"], f(st["mean_loss"], 2), f(st["mean_loss_pct"], 2), f(st["max_loss"], 2), f(st["max_loss_pct"], 2)))
    L += ["", "Окно 01.07–13.11 (136 сут.), 775 контрактов, ставка 500 $, комиссии Bybit по тарифу владельца; всё внутри окна — **«найдено на этих данных»**; протокол — летопись П-42 §13.49 (PS-914), критик `thr_n2ij7m7n7z` PASS на `4802b21b7`. Сутки ≥ 14.11 не читались.", "", "## Ответ", ""]
    for s in t.SIDES:
        v = V[s]
        cnt = {g: sum(1 for c in v["cells"].values() if c["gates"][g]) for g in "abcdefg"}
        L.append("- **%s:** %s. Испытаний 12 (Холм по обеим сторонам), шумовая планка по N = %d." % (SIDE[s], v["answer"], v["trials_noise"]))
        L.append("  Прошли ворота по отдельности (из 6): (а) минимум сделок %d, (б) Холм %d, (в) ≥ 4 из 5 месяцев %d, (г) без 2 суток / 2 монет %d, (д) охрана суммы %d, (е) плато %d, (ж) выше шума %d." % tuple(cnt[g] for g in "abcdefg"))
    L.append("")
    for s in t.SIDES:
        L += ["## %s — все клетки (описательно, ни одна не отброшена)" % SIDE[s].capitalize(), "", HDR, SEP, row("база", C[s]["base"])]
        for cid, _ in t.cells():
            L.append(row("`%s`" % cid, C[s]["cells"][cid], V[s]["cells"][cid]))
        g = G[s]
        fz = {c: r["forced"] for c, r in g.items() if r["forced"]}
        L += ["", "Ворота: G-base %s; G-dup групп %d; G-win: закрытых концом данных окна — %s; наибольшее удержание базы %s сут." % (
            "ok" if C[s]["gbase"] else "ОШИБКА", len(C[s]["gdup"]), fz if fz else "0", f(g["base"]["max_hold_d"], 2)), ""]
    L += ["## Ворота G-byte", "", "Базы `L_base` / `S_base` бинарём TK-649 (sha256 `%s…`) × wa / wb / wc: все csv и `summary.csv` байт в байт равны готовым базам h775, `meta.json` равен, кроме поля `tl: 0` в строке `params` — %s." % (B["bin_sha256"][:12], "ok" if B["ok"] else "ОШИБКА"), ""]
    open(os.path.join(out, NAME + ".md"), "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
