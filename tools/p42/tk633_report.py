#!/usr/bin/env python3
"""П-42 §13.33 (TK-633): таблицы отчёта в $ из `res/step1-<вселенная>.json` и `res/select33.json` (без пересчёта движка).
Использование: python3 tk633_report.py <каталог res/> — печатает markdown: клетки (Σ $, $/сделку, Sharpe, по месяцам) и ворота, ступень 1 чужой истории, неудачные пробои."""
from __future__ import annotations

import json
import os
import sys

MONTHS = ("июл", "авг", "сен", "окт", "ноя")
BUCKETS = ("0", "1", "2–3", "4+", "нет значения")
FAIL_BUCKETS = ("0", "1–2", "3–5", "6–10", "11+", "нет значения")
NAMES = {"L": "лонг", "S": "шорт"}
HIST = {"L": "шорт-история", "S": "лонг-история"}          # чужая история сделок стороны
UNIT = {("L", "X"): "ЛШ", ("S", "X"): "ШЛ", ("L", "I"): "ЛЛ-обр"}
UNI = {"h775": "честная вселенная h775 (775 контрактов, t24=12) — РЕШАЮЩАЯ", "o566": "старые 566 монет o566 — справочно"}


def money(x):
    return "%+.0f" % x if x is not None and x == x else "—"


def per(x):
    return "%+.2f" % x if x is not None and x == x else "—"


def sharpe(x):
    return "%.2f" % x if x is not None and x == x else "—"


def months(v):
    return " · ".join("%s (%d)" % (money(s), n) for n, s in v["months"])


def ci(v):
    lo, hi = v.get("lo90"), v.get("hi90")
    return "[%s; %s]" % (per(lo), per(hi)) if lo is not None and hi is not None else "—"


def row(name, v):
    mean = v["sum"] / v["n"] if v["n"] else float("nan")
    return "| %s | %d | %s | %s | %s | %s | %s |" % (name, v["n"], money(v["sum"]), per(mean), ci(v), sharpe(v["sharpe"]), months(v))


def step1_tables(root):
    out = []
    for u in ("h775", "o566"):
        d = json.load(open(os.path.join(root, "step1-%s.json" % u), encoding="utf-8"))
        out.append("#### %s\n" % UNI[u])
        for s in ("L", "S"):
            b = d[s]["base"]
            out.append("**%s**, база без фильтра: %d сделок, Σ %s $, %s $/сделку, Sharpe %s.\n" % (NAMES[s], b["n"], money(b["sum"]), per(b["sum"] / b["n"]), sharpe(b["sharpe"])))
            for D in (7, 14, 30):
                out.append("%s-сделки, D = %d сут., m = 1 NATR (%s: число успешных пробоев монеты другой стороны за D суток до сигнала):\n" % (NAMES[s], D, HIST[s]))
                out.append("| %s | сделок | Σ $ | $/сделку | 90 %% интервал $/сделку | Sharpe | Σ $ (сделок) по месяцам: %s |" % (HIST[s], " · ".join(MONTHS)))
                out.append("|---|---:|---:|---:|---|---:|---|")
                for name in BUCKETS:
                    out.append(row(name, d[s]["cross"]["xok10_d%d" % D][name]))
                out.append(row("**все (база)**", d[s]["cross"]["xok10_d%d" % D]["все"]))
                out.append("")
    return "\n".join(out)


def fail_tables(root):
    out = []
    for u in ("h775", "o566"):
        d = json.load(open(os.path.join(root, "step1-%s.json" % u), encoding="utf-8"))
        out.append("#### %s\n" % UNI[u])
        for s in ("L", "S"):
            for D in (7, 14, 30):
                out.append("%s-сделки, D = %d сут., m = 1 NATR (число НЕУДАЧНЫХ пробоев своей стороны — вернулись к уровню, не пройдя 1 NATR):\n" % (NAMES[s], D))
                out.append("| неудачных пробоев | сделок | Σ $ | $/сделку | 90 %% интервал $/сделку | Sharpe | Σ $ (сделок) по месяцам: %s |" % " · ".join(MONTHS))
                out.append("|---|---:|---:|---:|---|---:|---|")
                for name in FAIL_BUCKETS:
                    out.append(row(name, d[s]["fail"]["fail10_d%d" % D][name]))
                out.append(row("**все (база)**", d[s]["fail"]["fail10_d%d" % D]["все"]))
                out.append("")
    return "\n".join(out)


def yes(r, k):
    return {True: "да", False: "нет", None: "·"}[r.get(k)]


def cell_tables(root):
    sel = json.load(open(os.path.join(root, "select33.json"), encoding="utf-8"))
    h = sel["holm"]
    out = ["Холм совместный: m = %d (9 клеток §13.32 + %d новых пробных без дублей); p новых = %s.\n" % (h["m_joint"], h["m"], ", ".join("%.4f" % p for p in h["ps"]))]
    for part, key, sl in (("h775 — решающая", "rows", "slices_h775"), ("o566 — справочно (те же клетки, пороги c из h775)", "rows_o566", "slices_o566")):
        out.append("##### %s — деньги по клеткам ($, ставка 500 $)\n" % part)
        out.append("| единица · клетка (m·NATR, D, доля) | порог | сделок | Σ $ | $/сделку | Sharpe | Σ $ (сделок) по месяцам: %s |" % " · ".join(MONTHS))
        out.append("|---|---|---:|---:|---:|---:|---|")
        for s in ("L", "S"):
            b = sel[sl][s]["база"]
            mean = b["sum"] / b["n"] if b["n"] else float("nan")
            out.append("| **%s-сделки, база** | — | %d | %s | %s | %s | %s |" % (NAMES[s], b["n"], money(b["sum"]), per(mean), sharpe(b["sharpe"]), months(b)))
            for cid, r in sel[key][s].items():
                v = sel[sl][s].get(cid)
                if v is None:
                    continue
                mean = v["sum"] / v["n"] if v["n"] else float("nan")
                out.append("| %s · %s %s | %s | %d | %s | %s | %s | %s |" % (UNIT[(s, cid[0])], cid, "(проб.)" if r["trial"] else "(сосед)", "x%s %s" % ("≥" if cid[0] == "X" else "=", r["c"] if cid[0] == "X" else 0),
                                                                           v["n"], money(v["sum"]), per(mean), sharpe(v["sharpe"]), months(v)))
        out.append("")
        out.append("##### %s — разность к базе и ворота\n" % part)
        out.append("| единица · клетка | исключено сделок | Δ ± SE $/сделку | p | допуск | g1 (совм.) | g1 (новые) | g2 | g3 | g4 | g5 |")
        out.append("|---|---:|---|---:|---|---|---|---|---|---|---|")
        for s in ("L", "S"):
            for cid, r in sel[key][s].items():
                if r is None or r.get("d") is None:
                    continue
                out.append("| %s · %s %s | %d | %s ± %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                    UNIT[(s, cid[0])], cid, "(проб.)" if r["trial"] else "(сосед)", r["n_ex"], per(r["d"]), "%.2f" % r["se"], "%.3f" % r["p"] if r.get("p") is not None else "—",
                    {True: "да", False: "нет"}[r.get("admitted")], yes(r, "g1"), yes(r, "g1_new_only"), yes(r, "g2"), yes(r, "g3"), yes(r, "g4"), yes(r, "g5")))
        out.append("")
    out.append("**Ответ:** %s; исходы единиц: %s\n" % (sel["answer"], "; ".join("%s — %s" % (k, v[0]) for k, v in sel["outcomes"].items())))
    return "\n".join(out)


if __name__ == "__main__":
    root = sys.argv[1]
    print(cell_tables(root))
    print("\n---\n")
    print(step1_tables(root))
    print("\n---\n")
    print(fail_tables(root))
