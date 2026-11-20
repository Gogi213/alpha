#!/usr/bin/env python3
"""П-42 §13.34 (TK-634): таблицы отчёта в $ из `res/select34.json` и `res/step1-<вселенная>.json` (без пересчёта движка).
Использование: python3 tk634_report.py <каталог res/> — печатает markdown: деньги по клеткам ($, Sharpe, по месяцам), ворота (g1–g6), повтор на o566, ступень 1 (квартили, квадранты, контраст)."""
from __future__ import annotations

import json
import os
import sys

MONTHS = ("июл", "авг", "сен", "окт", "01–13.11")
NAMES = {"L": "лонг", "S": "шорт"}
FAM = {"IMP": "импульс", "FLOW": "поток"}
CTX = {"own": "своя история", "cross": "чужая история"}
UNI = {"h775": "честная вселенная h775 — РЕШАЮЩАЯ", "o566": "старые 566 монет o566 — справочно"}


def money(x):
    return "%+.0f" % x if x is not None and x == x else "—"


def per(x):
    return "%+.2f" % x if x is not None and x == x else "—"


def sh(x):
    return "%.2f" % x if x is not None and x == x else "—"


def mon(v):
    return " · ".join("%s (%d)" % (money(s), n) for n, s in v["months"])


def mon_sh(v):
    return " · ".join(sh(x) for x in v.get("sharpe_months", []))


def ci(v):
    lo, hi = v.get("lo90"), v.get("hi90")
    return "[%s; %s]" % (per(lo), per(hi)) if lo is not None and hi is not None else "—"


def cell_name(cid):
    p = cid.split("_")
    base = "%s · %s" % (FAM[p[0]], CTX[p[1]] if len(p) > 1 and p[1] in CTX else "только семейство")
    extra = {"q10": " (сосед q 0,10)", "q40": " (сосед q 0,40)", "m05": " (сосед m 0,5)", "m20": " (сосед m 2)"}.get(p[-1] if len(p) > 2 else "", "")
    return base + extra


def yes(r, k):
    return {True: "да", False: "нет", None: "·"}.get(r.get(k), "·")


def money_tables(sel, neighbors=False):
    """Первая таблица отчёта: по клеткам — сделок, Σ $, $/сделку, Sharpe годовой (суточный ряд), Σ $ и Sharpe по месяцам. Ставка 500 $. neighbors=False — база, пробные и справочные клетки; True — только соседи."""
    out = []
    for part, key, sl in (("h775 — решающая", "rows", "slices_h775"), ("o566 — справочно (те же клетки, `fv` и ECDF из h775)", "rows_o566", "slices_o566")):
        out.append("##### %s — деньги по клеткам ($, ставка 500 $; Sharpe — годовой, по суточному ряду)\n" % part)
        out.append("| сторона · клетка | вид | сделок | Σ $ | $/сделку | Sharpe | Σ $ (сделок) по месяцам: %s | Sharpe по месяцам |" % " · ".join(MONTHS))
        out.append("|---|---|---:|---:|---:|---:|---|---|")
        for s in ("L", "S"):
            b = sel[sl][s]["база"]
            out.append("| **%s, база** | — | %d | %s | %s | %s | %s | %s |" % (NAMES[s], b["n"], money(b["sum"]), per(b["mean"]), sh(b["sharpe"]), mon(b), mon_sh(b)))
            for cid, r in sel[key][s].items():
                v = sel[sl][s].get(cid)
                if v is None:
                    continue
                kind = "пробная" if r["trial"] else ("справ." if cid.endswith("_only") else "сосед")
                if (kind == "сосед") != neighbors:
                    continue
                out.append("| %s · %s | %s | %d | %s | %s | %s | %s | %s |" % (NAMES[s], cell_name(cid), kind, v["n"], money(v["sum"]), per(v["mean"]), sh(v["sharpe"]), mon(v), mon_sh(v)))
        out.append("")
    return "\n".join(out)


def gates_table(sel):
    h = sel["holm"]
    out = ["Холм совместный: m = %d (21 прежняя пробная клетка + %d новых); p новых = %s.\n" % (h["m_joint"], h["m"], ", ".join("%.4f" % p for p in h["ps"]))]
    out.append("##### h775 — разность к базе и ворота (g1 — совместный Холм; «новые» — Холм только по 8 новым, контекст)\n")
    out.append("| сторона · клетка | вид | исключено | Δ ± SE $/сделку | p | допуск | g1 | g1 (новые) | g2 | g3 | g4 | g5 | g6 | прошла |")
    out.append("|---|---|---:|---|---:|---|---|---|---|---|---|---|---|---|")
    for s in ("L", "S"):
        for cid, r in sel["rows"][s].items():
            if r is None or r.get("d") is None:
                continue
            kind = "пробная" if r["trial"] else ("справ." if cid.endswith("_only") else "сосед")
            out.append("| %s · %s | %s | %d | %s ± %.2f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                NAMES[s], cell_name(cid), kind, r["n_ex"], per(r["d"]), r["se"], "%.3f" % r["p"] if r.get("p") is not None else "—", "да" if r.get("admitted") else "нет",
                yes(r, "g1"), yes(r, "g1_new_only"), yes(r, "g2"), yes(r, "g3"), yes(r, "g4"), yes(r, "g5"), yes(r, "g6"), "**ДА**" if r.get("pass") else "нет"))
    out.append("")
    out.append("##### (g6) прибавка поверх частей: Δ клетки к «только история» и к «только семейство», $/сделку, точечно (описательно; «больше суммы частей» — только через эти две Δ ± SE и контраст ступени 1)\n")
    out.append("| сторона · клетка | Δ к «только история» ± SE | Δ к «только семейство» ± SE | g6 |")
    out.append("|---|---|---|---|")
    for s in ("L", "S"):
        for cid, g in sel["g6"][s].items():
            out.append("| %s · %s | %s ± %.2f | %s ± %.2f | %s |" % (NAMES[s], cell_name(cid), per(g["history"]["d"]), g["history"]["se"], per(g["family"]["d"]), g["family"]["se"], "да" if g["ok"] else "нет"))
    out.append("")
    out.append("##### o566 — справочно: Δ ± SE к базе, $/сделку (пробные и справочные клетки; допуск, Холм и плато не требуются)\n")
    out.append("| сторона · клетка | исключено | Δ ± SE | p | g2 | g3 | g4 |")
    out.append("|---|---:|---|---:|---|---|---|")
    for s in ("L", "S"):
        for cid, r in sel["rows_o566"][s].items():
            if r is None or r.get("d") is None or (not r["trial"] and not cid.endswith("_only")):
                continue
            out.append("| %s · %s | %d | %s ± %.2f | %s | %s | %s | %s |" % (NAMES[s], cell_name(cid), r["n_ex"], per(r["d"]), r["se"], "%.3f" % r["p"] if r.get("p") is not None else "—", yes(r, "g2"), yes(r, "g3"), yes(r, "g4")))
    out.append("")
    if sel.get("repl"):
        out.append("##### Повтор на o566 (g2–g4; плато и Холм не требуются)\n")
        for k, r in sel["repl"].items():
            out.append("- %s: Δ %s ± %.2f, g2 %s g3 %s g4 %s → **%s**" % (k, per(r["d"]), r["se"], yes(r, "g2"), yes(r, "g3"), yes(r, "g4"), "повторилась" if r["repl"] else "не повторилась"))
        out.append("")
    return "\n".join(out)


def answer_block(sel):
    lines = ["**Ответ: %s.**\n" % sel["answer"], "| единица | исход | клетки |", "|---|---|---|"]
    for k, (o, cells) in sel["outcomes"].items():
        lines.append("| %s | %s | %s |" % (k, o, ", ".join(cells) or "—"))
    return "\n".join(lines) + "\n"


def step1_tables(root):
    out = []
    for u in ("h775", "o566"):
        d = json.load(open(os.path.join(root, "step1-%s.json" % u), encoding="utf-8"))
        out.append("#### %s\n" % UNI[u])
        for s in ("L", "S"):
            b = d[s]["base"]
            out.append("**%s**, база: %d сделок, Σ %s $, %s $/сделку, Sharpe %s.\n" % (NAMES[s], b["n"], money(b["sum"]), per(b["mean"]), sh(b["sharpe"])))
            out.append("Квартили по краям ECDF h775 (композит — IMP / FLOW, затем компоненты):\n")
            out.append("| признак | Q1 | Q2 | Q3 | Q4 | нет значения |")
            out.append("|---|---|---|---|---|---|")
            for name, q in d[s]["quart"].items():
                cells = ["%s сд., %s $, %s $/сд., Sh %s" % (q["rows"][k]["n"], money(q["rows"][k]["sum"]), per(q["rows"][k]["mean"]) if q["rows"][k]["n"] else "—", sh(q["rows"][k]["sharpe"])) for k in ("Q1", "Q2", "Q3", "Q4", "нет значения")]
                out.append("| %s | %s |" % (name, " | ".join(cells)))
            out.append("")
            out.append("Квадранты «история × семейство» по пробным клеткам:\n")
            out.append("| клетка | история да · семейство да | история да · семейство нет | история нет · семейство да | история нет · семейство нет | контраст взаимодействия [90 %] |")
            out.append("|---|---|---|---|---|---|")
            for cid, q in d[s]["quad"].items():
                did = q["did"]
                cells = ["%d сд., %s $, %s $/сд." % (r["n"], money(r["sum"]), per(r["mean"]) if r["n"] else "—") for r in q["rows"].values()]
                out.append("| %s | %s | %s |" % (cell_name(cid), " | ".join(cells), "%s [%s; %s]" % (per(did["contrast"]), per(did["lo90"]), per(did["hi90"])) if did["contrast"] is not None else "—"))
            out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    root = sys.argv[1]
    sel = json.load(open(os.path.join(root, "select34.json"), encoding="utf-8"))
    print(answer_block(sel))
    print(money_tables(sel))
    print("\n---\n")
    print(gates_table(sel))
    print("\n---\n")
    print(step1_tables(root))
