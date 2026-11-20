#!/usr/bin/env python3
"""П-42 §13.54 (TK-654): таблицы отчёта по результатам юнита tk654-pnl.
  python3 tk654_report.py <каталог res TK-654 (cells54-def-<сторона>.json, cells54-<сторона>.json)> <файл вывода .md>
Доллары — ставка 500 $ на сделку; Sharpe — годовой по суточному ряду Σ $ за 136 суток; без бп, без медиан. Шумовой эталон — по ВСЕМ клеткам стороны (§13.54 п. 9)."""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))
import selection_bias as sb  # noqa: E402

MONTHS = ("июль", "август", "сентябрь", "октябрь", "01–13.11")
SIDE = {"L": "лонг", "S": "шорт"}
T_DAYS = 136
PRIOR = 103          # просмотрено раньше на тех же сутках (TK-638 + TK-639, на сторону) — контекст, не поправка
GROUPS = (("Чистый уровень / зона", ("PE", "ST"), ("CLEAN", "ZONE")),
          ("Резкий подход / проторговка", ("MV5", "CN60"), ("NOCONS", "SHARP")),
          ("Разреженность графика", ("ZV6", "TPM60"), ("DENSE",)),
          ("Пробиваемость монеты `bk` (видели на 566, §13.26)", (), ()))


def f(x, d=2, sign=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    s = ("%.*f" % (d, abs(x))).replace(".", ",")
    neg = x < 0 and float(s.replace(",", ".")) != 0
    return ("−" if neg else ("+" if sign and float(s.replace(",", ".")) != 0 else "")) + s


def noise(sharpe, trials):
    se = math.sqrt(365.0 / T_DAYS * (1 + sharpe * sharpe / 730.0))
    return sb.significance(sharpe, se, trials)


def fam_of(c):
    return "BK" if c["kind"] == "bk" else (c["cid"].split("_")[0] if c["kind"] == "lad" else c["cid"])


def row(name, c, side, base=False):
    st_ = c["rr"]["stops"]
    d = c.get("delta")
    dd = "—" if base else "%s [%s; %s]" % (f(d["d_mean"], 2, True), f(d["lo90"], 2, True), f(d["hi90"], 2, True))
    ds = "—" if base else "%s [%s; %s]" % (f(d["d_sum"], 0, True), f(d["sum_lo90"], 0, True), f(d["sum_hi90"], 0, True))
    ov = ""
    if side == "L":
        ov = " | " + " | ".join("—" if c.get(k) is None else "%s (%s %%)" % (f(c[k]["jaccard"], 2), f(100 * c[k]["share_cell_in_ref"], 0)) for k in ("L_n24", "L_mk2"))
    return "| %s | %d%s | %s | %s | %s | %s | %s | %s | %s | %s | %s%s |" % (
        name, c["n"], " мало" if c["few"] else "", f(c["sum"], 1, True), f(c["mean"], 2, True), f(c["sharpe"], 2, True), dd, ds,
        " / ".join(f(m[1], 0, True) + ("*" if c["months_few"][i] else "") for i, m in enumerate(c["months"])), f(c["rr"]["rr"]),
        f(st_["mean_loss_pct"], 1) if st_["n"] else "нет стопов", f(c["maxdd"], 1), ov)


def head(side):
    h = "| клетка | сделок | Σ $ | $ на сделку | Шарп | Δ $/сд. к базе [90 %] | Δ Σ $ к базе [90 %] | Σ $ по месяцам (" + " / ".join(MONTHS) + ") | R:R | стоп, % маржи | просадка, $ |"
    if side == "L":
        h += " J с `L_n24` (доля в нём) | J с `L_mk2` (доля в нём) |"
    return h + "\n|" + "---|" * (h.count("|") - 1)


def name_of(c):
    s = "`%s` %s" % (c["cid"], c["label"])
    if c.get("also"):
        s += " (= %s)" % ", ".join("`%s`" % x for x in c["also"])
    return s


def neighbours(defs, cid):
    by = {}
    for c in defs:
        for k in [c["cid"]] + c.get("also", []):
            by[k] = c["cid"]
    me = next(c for c in defs if c["cid"] == cid)
    out = []
    if me["kind"] == "lad":
        fam, d, q = me["cid"].split("_")
        qi = int(q[1:])
        for k in ("%s_%s_q%02d" % (fam, d, qi - 20), "%s_%s_q%02d" % (fam, d, qi + 20), "%s_%s_q%02d" % (fam, "le" if d == "ge" else "ge", qi)):
            if k in by and by[k] != cid:
                out.append((k, by[k]))
    elif me["kind"] == "bk":
        Ks, Ts = (20, 40, 60), (0.02, 0.035, 0.05)
        i, j = Ks.index(me["K"]), Ts.index(me["tau"])
        for a, b in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)):
            k = "K%d_t%03d" % (Ks[a], round(Ts[b] * 1000)) if 0 <= a < 3 and 0 <= b < 3 else None
            if k in by and by[k] != cid:
                out.append((k, by[k]))
    return out


def build(res, out):
    L = []
    for side in "LS":
        D = json.load(open(os.path.join(res, "cells54-def-%s.json" % side), encoding="utf-8"))
        R = json.load(open(os.path.join(res, "cells54-%s.json" % side), encoding="utf-8"))
        defs, cells, b = D["cells"], R["cells"], R["base"]
        L.append("## %s (h775, база %d сделок, Σ %s $, Шарп %s)\n" % (SIDE[side], b["n"], f(b["sum"], 2, True), f(b["sharpe"], 2)))
        for title, fams, named in GROUPS:
            L.append("### %s\n" % title)
            L.append(head(side))
            L.append(row("**база (без фильтра)**", b, side, base=True))
            for c in defs:
                fm = fam_of(c)
                if (title.startswith("Пробиваемость") and fm == "BK") or fm in fams or fm in named:
                    L.append(row(name_of(c), cells[c["cid"]], side))
            L.append("")
        if D["same_as_base"]:
            L.append("≡ база (ничего не исключают, не прогонялись): %s.\n" % ", ".join("`%s`" % x for x in D["same_as_base"]))
        n_cells = len(cells)
        best = max(cells.items(), key=lambda kv: kv[1]["sharpe"])
        nz = noise(best[1]["sharpe"], n_cells)
        nz_all = noise(best[1]["sharpe"], n_cells + PRIOR)
        L.append("**Шумовой эталон (%s):** клеток стороны %d (после слияния дублей); наибольший Шарп — `%s` %s (n %d, Σ %s $); ожидаемый максимум шума %s, дефлированный Шарп %s, значим (survives) — %s. С прежними %d клетками TK-638/639 (контекст): планка %s, дефлированный %s.\n" % (
            SIDE[side], n_cells, best[0], f(best[1]["sharpe"], 2), best[1]["n"], f(best[1]["sum"], 1, True), f(nz["noise_bar"]), f(nz["deflated"]), "да" if nz["survives"] else "нет",
            PRIOR, f(nz_all["noise_bar"]), f(nz_all["deflated"])))
        nb = neighbours(defs, best[0])
        if nb:
            L.append("Соседи `%s`: %s.\n" % (best[0], "; ".join("`%s` Шарп %s, Δ Σ %s $" % (k, f(cells[v]["sharpe"], 2, True), f(cells[v]["delta"]["d_sum"], 0, True)) for k, v in nb)))
    open(out, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
