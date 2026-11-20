#!/usr/bin/env python3
"""П-42 §13.57 (TK-657): таблицы отчёта по результатам юнита tk657-pnl.
  python3 tk657_report.py <каталог res (cells57-S.json, verdict57.json)>  — печатает markdown (база, клетки A1 / A2 / B, ворота). Доллары — ставка 500 $; Sharpe — годовой; без бп, без медиан."""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def f(x, d=2, sign=False):
    """Как `tk643_report.f` (тот модуль читает argv при импорте)."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    s = ("%.*f" % (d, abs(x))).replace(".", ",")
    neg = x < 0 and float(s.replace(",", ".")) != 0
    return ("−" if neg else ("+" if sign and float(s.replace(",", ".")) != 0 else "")) + s


def stop_txt(r):
    s = r["rr"]["stops"]
    return "%d · %s $ (%s %%)" % (s["n"], f(s["mean_loss"], 1), f(s["mean_loss_pct"], 1)) if s["n"] else "нет"


MONTHS = ("июль", "август", "сентябрь", "октябрь", "01–13.11")


def gates(g):
    return "".join(k if v else "·" for k, v in g.items())


def main(res):
    C = json.load(open(os.path.join(res, "cells57-S.json"), encoding="utf-8"))
    V = json.load(open(os.path.join(res, "verdict57.json"), encoding="utf-8"))
    b = C["base"]
    out = []
    out.append("База `S_base` h775: %d сделок, Σ %s $, %s $/сделку, %s $/сут., Sharpe %s, R:R %s, стопов %s; просадка %s $." % (
        b["n"], f(b["sum"], 2, True), f(b["mean"], 3, True), f(b["sum"] / 136, 2, True), f(b["sharpe"], 2, True), f(b["rr"]["rr"]), stop_txt(b), f(b["maxdd"], 1)))
    out.append("")
    out.append("| клетка | сделок | Σ $ | $/сделку | $/сут. | Sharpe | Δ $/сделку [нижн. 5 %] | p (A1/B) | ΔΣ $ ± SE | p ΔΣ (A2) | ΔΣ по месяцам | + сд. / Σ | − сд. / Σ | общие ΔΣ | R:R | стопов · убыток | ворота A1/B | ворота A2 |")
    out.append("|" + "---|" * 18)
    for cid, r in C["cells"].items():
        d = r["delta"]
        a2 = r.get("a2")
        dm = a2["months"] if a2 else r["dmonths"]
        dc = r["decomp"]
        g1 = V["A1B"]["cells"][cid]["gates"]
        g2 = V["A2"]["cells"][cid]["gates"] if cid in V["A2"]["cells"] else None
        out.append("| `%s`%s | %d | %s | %s | %s | %s | %s [%s] | %.4f | %s ± %s | %s | %s | %d / %s | %d / %s | %s | %s | %s | %s | %s |" % (
            cid, " (не действует)" if r["inert"] else "", r["n"], f(r["sum"], 1, True), f(r["mean"], 3, True), f(r["sum"] / 136, 2, True), f(r["sharpe"], 2, True),
            f(d["d"], 3, True), f(d["lower"], 3, True), d["p"], f(d["dsum"], 1, True), f(d["se_sum"], 1),
            "%.4f" % a2["p"] if a2 else "—", " / ".join(f(x, 0, True) for x in (a2["months"] if a2 else [None] * 5)) if a2 else "—",
            dc["added_n"], f(dc["added_sum"], 1, True), dc["removed_n"], f(dc["removed_sum"], 1, True), f(dc["common_dsum"], 1, True),
            f(r["rr"]["rr"]), stop_txt(r), gates(g1), gates(g2) if g2 else "—"))
    out.append("")
    for name in ("A1B", "A2"):
        v = V[name]
        ex = next(iter(v["cells"].values()))["noise"]
        top = max(v["cells"].items(), key=lambda kv: kv[1]["noise"]["sharpe"])
        out.append("- **%s:** m = %d, N шума = %d, планка Sharpe %s; наибольший Sharpe `%s` %s (дефл. %s); %s. Ворота по отдельности: %s." % (
            name, v["m"], v["trials_noise"], f(ex["noise_bar"], 2), top[0], f(top[1]["noise"]["sharpe"], 2), f(top[1]["noise"]["deflated"], 2), v["answer"],
            ", ".join("(%s) %d" % (g, sum(1 for c in v["cells"].values() if c["gates"][g])) for g in "abcdefg")))
    out.append("- не действует: %s; G-dup: %s; forced у базы %d, у клеток %s." % (C["inert"] or "нет", C["gdup"] or "нет", C["gwin_forced_base"], C["gwin_forced_cells"] or "нет"))
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv[1])
