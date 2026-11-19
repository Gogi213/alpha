#!/usr/bin/env python3
"""П-42 §13.32 (TK-625): таблицы отчёта в $ из `res/step1-<вселенная>.json` и `res/select.json` (без пересчёта движка).
Использование: python3 tk625_report.py <каталог с step1-*.json и select.json> [m=10] — печатает markdown-таблицы ступени 1 и клеток."""
from __future__ import annotations

import json
import os
import sys

MONTHS = ("июл", "авг", "сен", "окт", "ноя")
BUCKETS = ("0", "1", "2–3", "4+", "нет значения")
NAMES = {"L": "лонг", "S": "шорт"}
UNI = {"h775": "честная вселенная h775 (775 контрактов, t24=12) — РЕШАЮЩАЯ", "o566": "старые 566 монет o566 — справочно"}


def money(x):
    return "%+.0f" % x if x == x else "—"


def per(x):
    return "%+.2f" % x if x == x else "—"


def sharpe(x):
    return "%.2f" % x if x == x else "—"


def row(name, v):
    mon = " · ".join("%s (%d)" % (money(s), n) for n, s in v["months"])
    mean = v["sum"] / v["n"] if v["n"] else float("nan")
    return "| %s | %d | %s | %s | %s | %s |" % (name, v["n"], money(v["sum"]), per(mean), sharpe(v["sharpe"]), mon)


def step1_tables(root, m):
    out = []
    for u in ("h775", "o566"):
        d = json.load(open(os.path.join(root, "step1-%s.json" % u), encoding="utf-8"))
        out.append("#### %s\n" % UNI[u])
        for s in ("L", "S"):
            b = d[s]["base"]
            out.append("**%s**, база без фильтра: %d сделок, Σ %s $, %s $/сделку, Sharpe %s.\n" % (NAMES[s], b["n"], money(b["sum"]), per(b["sum"] / b["n"]), sharpe(b["sharpe"])))
            for D in (7, 14, 30):
                col = "ok%s_d%d" % (m, D)
                out.append("%s, D = %d сут., m = %s NATR (число минимально успешных пробоев монеты за D суток до сигнала):\n" % (NAMES[s], D, {"05": "0,5", "10": "1", "20": "2"}[m]))
                out.append("| `n_ok` | сделок | Σ $ | $/сделку | Sharpe | Σ $ (сделок) по месяцам: %s |" % " · ".join(MONTHS))
                out.append("|---|---:|---:|---:|---:|---|")
                for name in BUCKETS:
                    out.append(row(name, d[s]["cols"][col][name]))
                out.append(row("**все (база)**", d[s]["cols"][col]["все"]))
                out.append("")
    return "\n".join(out)


def intervals(root, m):
    out = ["| вселенная · сторона · D · `n_ok` | сделок | $/сделку | 90 % интервал $/сделку (бутстреп по суткам, блок 4) |", "|---|---:|---:|---|"]
    for u in ("h775",):
        d = json.load(open(os.path.join(root, "step1-%s.json" % u), encoding="utf-8"))
        for s in ("L", "S"):
            for D in (7, 14, 30):
                col = "ok%s_d%d" % (m, D)
                for name in BUCKETS + ("все",):
                    v = d[s]["cols"][col][name]
                    if v["n"]:
                        iv = "[%s; %s]" % (per(v["lo90"]), per(v["hi90"])) if v.get("lo90") is not None else "—"
                        out.append("| %s · %s · D%d · %s | %d | %s | %s |" % (u, NAMES[s], D, name, v["n"], per(v["sum"] / v["n"]), iv))
    return "\n".join(out)


def cell_tables(root):
    sel = json.load(open(os.path.join(root, "select.json"), encoding="utf-8"))
    out = ["Холм: пробных клеток без дублей m = %d; p = %s.\n" % (sel["holm"]["m"], ", ".join("%.4f" % p for p in sel["holm"]["ps"]))]
    hdr = "| сторона · клетка (m·NATR, D, доля, порог c) | исключено сделок | сделок клетки | Σ клетки $ | $/сделку клетки | Δ ± SE $/сделку | p | g1 | g2 | g3 | g4 | g5 |"
    sep = "|---|---:|---:|---:|---:|---|---:|---|---|---|---|---|"
    for part, key in (("h775 — решающая (пробные клетки и соседи для плато)", "rows"), ("o566 — справочно (те же клетки, пороги c из h775)", "rows_o566")):
        out.append("**%s**\n" % part)
        out.append(hdr)
        out.append(sep)
        for s in ("L", "S"):
            for cid, r in sel[key][s].items():
                if r is None or r.get("d") is None:
                    continue
                mean = r["s_c"] / r["n_c"] if r["n_c"] else None
                yes = lambda k: {True: "да", False: "нет", None: "·"}[r.get(k)]       # noqa: E731
                out.append("| %s · %s %s | %d | %d | %s | %s | %s ± %s | %s | %s | %s | %s | %s | %s |" % (
                    NAMES[s], cid, "(проб.)" if r["trial"] else "(сосед)", r["n_ex"], r["n_c"], money(r["s_c"]), per(mean) if mean is not None else "—",
                    per(r["d"]), "%.2f" % r["se"], "%.3f" % r["p"] if r.get("p") is not None else "—", yes("g1"), yes("g2"), yes("g3"), yes("g4"), yes("g5")))
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    root = sys.argv[1]
    m = sys.argv[2] if len(sys.argv) > 2 else "10"
    print(step1_tables(root, m))
    print("\n---\n")
    print(intervals(root, m))
    print("\n---\n")
    print(cell_tables(root))
