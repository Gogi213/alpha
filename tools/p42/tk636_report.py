#!/usr/bin/env python3
"""П-42 §13.35 (TK-636): таблицы отчёта в $ из `res/select36.json` и `res/step1-<вселенная>.json` (без пересчёта движка).
Использование: python3 tk636_report.py <каталог res/> — печатает markdown: R:R баз (первой строкой), ответ по сторонам, по группам признаков, деньги по клеткам ($, Sharpe, по месяцам), ворота (g1–g5), повтор на o566, ступень 1."""
from __future__ import annotations

import json
import os
import sys

MONTHS = ("июл", "авг", "сен", "окт", "01–13.11")
NAMES = {"L": "лонг", "S": "шорт"}
UNI = {"h775": "честная вселенная h775 — РЕШАЮЩАЯ", "o566": "старые 566 монет o566 — справочно"}
KIND = {"T": "пробная", "N": "сосед", "R": "справ."}
# Срез 0,3 даёт один крупный кластер (55–205 монет) и один средний: cc и PEER — связь с широкой группой рынка, не сектор (критик, 20.11).
FAM_LABEL = {"соседи по кластеру (PEER)": "связь с широкой группой рынка: соседи по кластеру (PEER)"}
TOV_IDS = ("TOV_16", "TOV_24", "TOV_36", "TOV_54", "TOV_80")


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


def rr_txt(v):
    r = v.get("rr")
    if not r:
        return "—"
    return "R:R %s, стоп %s сд. (%s %% маржи)" % (sh(r["rr"]), r["stops"], "%.2f" % r["stop_loss_pct"] if r["stop_loss_pct"] is not None and r["stop_loss_pct"] == r["stop_loss_pct"] else "—")


def kind_of(cid, r):
    if r["trial"]:
        return "T"
    return "R" if cid == "CLS" else "N"


def yes(r, k):
    return {True: "да", False: "нет", None: "·"}.get(r.get(k), "·")


def rr_first(root):
    out = ["**R:R баз и убыток по стопу при плече 2 (маржа 250 $; печатается первым, п. 3 протокола):**\n", "| вселенная · сторона | сделок | Σ $ | R:R | стоп-выходов | убыток стопа, % маржи |", "|---|---:|---:|---:|---:|---:|"]
    for u in ("h775", "o566"):
        d = json.load(open(os.path.join(root, "step1-%s.json" % u), encoding="utf-8"))
        for s in ("L", "S"):
            b = d[s]["base"]
            r = b["rr"]
            out.append("| %s · %s | %d | %s | %s | %d | %s |" % (u, NAMES[s], b["n"], money(b["sum"]), sh(r["rr"]), r["stops"], "%.2f" % r["stop_loss_pct"]))
    return "\n".join(out) + "\n"


def answer_block(sel):
    lines = ["**Ответ: %s.**\n" % sel["answer"], "| сторона | исход | клетки |", "|---|---|---|"]
    for k, (o, cells) in sel["outcomes"].items():
        lines.append("| %s | %s | %s |" % (k, o, ", ".join(cells) or "—"))
    lines.append("")
    lines.append("По группам признаков (h775, найдено на этих данных; Δ ± SE лучшей по Δ клетки группы — область, не «лучший вариант»):\n")
    lines.append("| сторона · группа | пробных | допущено | g1 | g2 | g3 | g4 | g5 | лучшая по Δ: Δ ± SE $/сделку |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
    for s in ("L", "S"):
        for fam, g in sel["groups"][s].items():
            b = g["best"]
            lines.append("| %s · %s | %d | %d | %d | %d | %d | %d | %d | %s |" % (NAMES[s], FAM_LABEL.get(fam, fam), g["trial"], g["admitted"], g["g1"], g["g2"], g["g3"], g["g4"], g["g5"],
                                                                   "%s ± %.2f (%s)" % (per(b["d"]), b["se"], b["cid"]) if b else "—"))
    return "\n".join(lines) + "\n"


def g5_reach(root, s):
    """(g5) по построению (counts, без P&L): клетка TOV_X достижима, если у неё ≥ 2 допущенных соседа с J ≤ 0,8."""
    c = json.load(open(os.path.join(root, "counts36-h775-%s.json" % s), encoding="utf-8"))
    adm = {x["cid"]: x["admit_floor"] for x in c["cells"]}
    return {cid: sum(1 for nb, j in c["jaccard"][cid].items() if adm.get(nb) and j <= 0.8) >= 2 for cid in TOV_IDS}


def tov_ladder(sel, root):
    """Все пять уровней TOV_X одной лесенкой (просьба владельца: перебор суточного оборота при фиксированном часовом t1h = 0,5 млн $); не только лучший."""
    out = ["##### Лесенка суточного оборота TOV_X (торгуются только сделки с `turn24h ≥ X` млн $; ставка 500 $; Sharpe — годовой по суточному ряду; найдено на этих данных)\n",
           "Помечено «виден P&L до записи» (эффект ключа `t24 = 12`, TK-621): максимум исхода — «кандидат для вперёд», не «работает». **На o566 нет ключа `t24 = 12`, поэтому повтор TOV на o566 по построению близок к подтверждению (тот же эффект `t24`, уже прочитанный в TK-621, U0 → U1) — не независимое подтверждение.** Колонка «g5 по построению» — по `counts` (≥ 2 допущенных соседа с J ≤ 0,8), до P&L.\n"]
    for s in ("L", "S"):
        reach = g5_reach(root, s)
        out.append("**%s, h775 (решающая)**\n" % NAMES[s])
        out.append("| ступень | g5 по построению | сделок | Σ $ | $/сделку | Sharpe | Δ ± SE $/сделку | p | допуск | g1 | g2 | g3 | g4 | g5 | прошла | Σ $ (сделок) по месяцам: %s | Sharpe по месяцам |" % " · ".join(MONTHS))
        out.append("|---|---|---:|---:|---:|---:|---|---:|---|---|---|---|---|---|---|---|---|")
        b = sel["slices_h775"][s]["база"]
        out.append("| база (ключ 12 млн $) | — | %d | %s | %s | %s | — | — | — | — | — | — | — | — | — | %s | %s |" % (b["n"], money(b["sum"]), per(b["mean"]), sh(b["sharpe"]), mon(b), mon_sh(b)))
        for cid in TOV_IDS:
            r, v = sel["rows"][s].get(cid), sel["slices_h775"][s].get(cid)
            if r is None or v is None:
                continue
            out.append("| %s млн $ | %s | %d | %s | %s | %s | %s ± %.2f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                cid[4:], "да" if reach[cid] else "нет", v["n"], money(v["sum"]), per(v["mean"]), sh(v["sharpe"]), per(r["d"]), r["se"],
                "%.4f" % r["p"] if r.get("p") is not None else "—", "да" if r.get("admitted") else "нет",
                yes(r, "g1"), yes(r, "g2"), yes(r, "g3"), yes(r, "g4"), yes(r, "g5"), "**ДА**" if r.get("pass") else "нет", mon(v), mon_sh(v)))
        out.append("")
        out.append("**%s, o566 (справочно; без ключа `t24 = 12`)**\n" % NAMES[s])
        out.append("| ступень | сделок | Σ $ | $/сделку | Sharpe | Δ ± SE $/сделку | g2 | g3 | g4 |")
        out.append("|---|---:|---:|---:|---:|---|---|---|---|")
        b = sel["slices_o566"][s]["база"]
        out.append("| база | %d | %s | %s | %s | — | — | — | — |" % (b["n"], money(b["sum"]), per(b["mean"]), sh(b["sharpe"])))
        for cid in TOV_IDS:
            r, v = sel["rows_o566"][s].get(cid), sel["slices_o566"][s].get(cid)
            if r is None or v is None or r.get("d") is None:
                continue
            out.append("| %s млн $ | %d | %s | %s | %s | %s ± %.2f | %s | %s | %s |" % (cid[4:], v["n"], money(v["sum"]), per(v["mean"]), sh(v["sharpe"]), per(r["d"]), r["se"], yes(r, "g2"), yes(r, "g3"), yes(r, "g4")))
        out.append("")
    return "\n".join(out) + "\n"


def money_tables(sel, kinds):
    """По клеткам — сделок, Σ $, $/сделку, Sharpe годовой (суточный ряд), Σ $ и Sharpe по месяцам. Ставка 500 $. kinds — какие виды клеток печатать (T / N / R)."""
    out = []
    for part, key, sl in (("h775 — решающая", "rows", "slices_h775"), ("o566 — справочно (те же клетки, пороги и ECDF из h775)", "rows_o566", "slices_o566")):
        out.append("##### %s — деньги по клеткам ($, ставка 500 $; Sharpe — годовой, по суточному ряду)\n" % part)
        out.append("| сторона · клетка | вид | сделок | Σ $ | $/сделку | Sharpe | 90 %% интервал $/сделку | Σ $ (сделок) по месяцам: %s | Sharpe по месяцам |" % " · ".join(MONTHS))
        out.append("|---|---|---:|---:|---:|---:|---|---|---|")
        for s in ("L", "S"):
            b = sel[sl][s]["база"]
            out.append("| **%s, база** | — | %d | %s | %s | %s | %s | %s | %s |" % (NAMES[s], b["n"], money(b["sum"]), per(b["mean"]), sh(b["sharpe"]), ci(b), mon(b), mon_sh(b)))
            for cid, r in sel[key][s].items():
                v = sel[sl][s].get(cid)
                if v is None:
                    continue
                k = kind_of(cid, r)
                if k not in kinds:
                    continue
                out.append("| %s · %s | %s | %d | %s | %s | %s | %s | %s | %s |" % (NAMES[s], cid, KIND[k], v["n"], money(v["sum"]), per(v["mean"]), sh(v["sharpe"]), ci(v), mon(v), mon_sh(v)))
        out.append("")
    return "\n".join(out)


def gates_table(sel):
    h = sel["holm"]
    out = ["Холм совместный (семейство = совместное, 29 + m_new; критик thr_sia32k2tr5, 20.11): m = %d (29 прежних пробных + %d новых); p новых = %s.\n" % (h["m_joint"], h["m"], ", ".join("%.4f" % p for p in h["ps"]))]
    out.append("##### h775 — разность к базе и ворота (g1 — совместный Холм; «новые» — Холм только по новым клеткам, «сторона» — Холм по стороне; контекст)\n")
    out.append("| сторона · клетка | вид | исключено | Δ ± SE $/сделку | p | допуск | g1 | g1 (новые) | g1 (сторона) | g2 | g3 | g4 | g5 | прошла |")
    out.append("|---|---|---:|---|---:|---|---|---|---|---|---|---|---|---|")
    for s in ("L", "S"):
        for cid, r in sel["rows"][s].items():
            if r is None or r.get("d") is None:
                continue
            out.append("| %s · %s | %s | %d | %s ± %.2f | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                NAMES[s], cid, KIND[kind_of(cid, r)], r["n_ex"], per(r["d"]), r["se"], "%.4f" % r["p"] if r.get("p") is not None else "—", "да" if r.get("admitted") else "нет",
                yes(r, "g1"), yes(r, "g1_new_only"), yes(r, "g1_side"), yes(r, "g2"), yes(r, "g3"), yes(r, "g4"), yes(r, "g5"), "**ДА**" if r.get("pass") else "нет"))
    out.append("")
    out.append("##### o566 — справочно: Δ ± SE к базе, $/сделку (пробные и справочная клетки; допуск, Холм и плато не требуются)\n")
    out.append("| сторона · клетка | исключено | Δ ± SE | p | g2 | g3 | g4 |")
    out.append("|---|---:|---|---:|---|---|---|")
    for s in ("L", "S"):
        for cid, r in sel["rows_o566"][s].items():
            if r is None or r.get("d") is None or kind_of(cid, r) == "N":
                continue
            out.append("| %s · %s | %d | %s ± %.2f | %s | %s | %s | %s |" % (NAMES[s], cid, r["n_ex"], per(r["d"]), r["se"], "%.4f" % r["p"] if r.get("p") is not None else "—", yes(r, "g2"), yes(r, "g3"), yes(r, "g4")))
    out.append("")
    if sel.get("repl"):
        out.append("##### Повтор на o566 (g2–g4; плато и Холм не требуются)\n")
        for k, r in sel["repl"].items():
            out.append("- %s: Δ %s ± %.2f, g2 %s g3 %s g4 %s → **%s**" % (k, per(r["d"]), r["se"], yes(r, "g2"), yes(r, "g3"), yes(r, "g4"), "повторилась" if r["repl"] else "не повторилась"))
        out.append("")
    return "\n".join(out)


def slice_line(v):
    return "%d сд., Σ %s $, %s $/сд. %s, Sh %s; %s; %s" % (v["n"], money(v["sum"]), per(v["mean"]) if v["n"] else "—", ci(v), sh(v["sharpe"]), mon(v), rr_txt(v))


def step1_tables(root):
    out = []
    for u in ("h775", "o566"):
        d = json.load(open(os.path.join(root, "step1-%s.json" % u), encoding="utf-8"))
        out.append("#### %s\n" % UNI[u])
        for s in ("L", "S"):
            b = d[s]["base"]
            out.append("**%s**, база: %s.\n" % (NAMES[s], slice_line(b)))
            out.append("Срезы (срез ≠ отдельная стратегия: пауза портфеля и занятость в срезе не пересчитываются):\n")
            out.append("| срез | сделок · Σ $ · $/сд. [90 %] · Sharpe · по месяцам · R:R |")
            out.append("|---|---|")
            for grp, title in (("cls", "класс"), ("typ", "тип актива"), ("tag", "тег")):
                for nm, v in d[s][grp].items():
                    out.append("| %s: %s | %s |" % (title, nm, slice_line(v)))
            out.append("")
            out.append("Квартили по краям ECDF h775 (TOV, AGE, VOL, затем компоненты и COUP по окнам; 7d — справка):\n")
            out.append("| признак (границы) | Q1 | Q2 | Q3 | Q4 | нет значения |")
            out.append("|---|---|---|---|---|---|")
            for name, qd in d[s]["quart"].items():
                cells = ["%s сд., %s $, %s $/сд., Sh %s" % (qd["rows"][k]["n"], money(qd["rows"][k]["sum"]), per(qd["rows"][k]["mean"]) if qd["rows"][k]["n"] else "—", sh(qd["rows"][k]["sharpe"]))
                         for k in ("Q1", "Q2", "Q3", "Q4", "нет значения")]
                out.append("| %s (%s) | %s |" % (name, "; ".join("%.4g" % e for e in qd["edges"]), " | ".join(cells)))
            out.append("")
            out.append("PEER (число соседей по кластеру с пересечением за окно):\n")
            out.append("| вариант · корзина | сделок · Σ $ · $/сд. [90 %] · Sharpe · по месяцам · R:R |")
            out.append("|---|---|")
            for nm, rows in d[s]["peer"].items():
                for k, v in rows.items():
                    out.append("| %s · %s | %s |" % (nm, k, slice_line(v)))
            out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    root = sys.argv[1]
    sel = json.load(open(os.path.join(root, "select36.json"), encoding="utf-8"))
    print(rr_first(root))
    print(answer_block(sel))
    print(tov_ladder(sel, root))
    print(money_tables(sel, ("T", "R")))
    print("\n---\n")
    print("Соседи (плато, не пробные):\n")
    print(money_tables(sel, ("N",)))
    print("\n---\n")
    print(gates_table(sel))
    print("\n---\n")
    print(step1_tables(root))
