#!/usr/bin/env python3
"""П-42 §13.37 (TK-639): отчёт и приложение по результатам юнита tk639-pnl.
  python3 tk639_report.py <каталог res TK-639 (cells39-*.json, counts39-*.json, thr39.json, gate-n39.json)> <каталог res TK-638 (cells38-*.json)> <каталог вывода>
Пишет <вывод>/p42-tradecount-size-2026-11-20.md (отчёт) и <вывод>/p42-tradecount-size-2026-11-20/cells-all.md (все клетки со всеми колонками).
Доллары — ставка 500 $ на сделку; Sharpe — годовой по суточному ряду Σ $ за 136 суток; без бп, без медиан."""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))
import selection_bias as sb  # noqa: E402

RES9, RES8, OUT = sys.argv[1:4]
MONTHS = ("июль", "август", "сентябрь", "октябрь", "01–13.11")
SIDE = {"L": "лонг", "S": "шорт"}
UNI = {"h775": "h775 (честная вселенная, 775 контрактов; база с `t24 = 12`)", "o566": "o566 (прежние 566 монет; в базе нет ключа D)"}
MIN_TRADES = 70
T_DAYS = 136
N_TK638 = 71
N_TK639 = 32
N_ALL = N_TK638 + N_TK639
FAMS = (("NC", "Число сделок монеты за 6 ч и за 12 ч", ("NC360", "NC720")),
        ("SZ60", "Средний размер сделки за 1 ч (оборот / число сделок)", ("SZ60",)),
        ("SZ360", "Средний размер сделки за 6 ч", ("SZ360",)),
        ("SZ1440", "Средний размер сделки за 24 ч", ("SZ1440",)))
FAM_TXT = {"NC360": "сделок за 6 ч", "NC720": "сделок за 12 ч", "SZ60": "размер сделки за 1 ч", "SZ360": "размер сделки за 6 ч", "SZ1440": "размер сделки за 24 ч"}


def f(x, d=2, sign=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    s = ("%.*f" % (d, abs(x))).replace(".", ",")
    neg = x < 0 and float(s.replace(",", ".")) != 0
    return ("−" if neg else ("+" if sign and float(s.replace(",", ".")) != 0 else "")) + s


def load():
    R = {(u, s): json.load(open(os.path.join(RES9, "cells39-%s-%s.json" % (u, s)), encoding="utf-8")) for u in ("h775", "o566") for s in "LS"}
    CN = {(u, s): json.load(open(os.path.join(RES9, "counts39-%s-%s.json" % (u, s)), encoding="utf-8")) for u in ("h775", "o566") for s in "LS"}
    R8 = {(u, s): json.load(open(os.path.join(RES8, "cells38-%s-%s.json" % (u, s)), encoding="utf-8")) for u in ("h775", "o566") for s in "LS"}
    thr = json.load(open(os.path.join(RES9, "thr39.json"), encoding="utf-8"))
    gate = json.load(open(os.path.join(RES9, "gate-n39.json"), encoding="utf-8"))
    return R, CN, R8, thr, gate


def label(c):
    sign = "≤" if c["dir"] == "le" else "≥"
    num = ("{:,}".format(int(c["N"])).replace(",", " ")) if c["fam"].startswith("NC") or c["N"] >= 100 else ("%g" % c["N"]).replace(".", ",")
    unit = "" if c["fam"].startswith("NC") else " $"
    return "%s %s %s%s (q%s)" % (FAM_TXT[c["fam"]], sign, num, unit, "/".join("%d" % round(p * 100) for p in c["ps"]))


def noise(sharpe, trials=N_ALL):
    se = math.sqrt(365.0 / T_DAYS * (1 + sharpe * sharpe / 730.0))
    return se, sb.significance(sharpe, se, trials)


def ov_txt(c):
    """Пересечение сделок клетки с замороженным `L_n24` (только лонг): Jaccard и доля сделок клетки внутри `L_n24`."""
    o = c.get("ln24")
    return "—" if o is None else "%s (%s %%)" % (f(o["jaccard"], 2), f(100 * o["share_cell_in_ref"], 0))


def row_cells(name, c, base_share=False, with_ov=False):
    st = c["rr"]["stops"]
    return "| %s | %d%s | %s | %s | %s | %s…%s | %s | %s | %s | %s |" % (
        name, c["n"], " мало" if c["few"] else "", f(c["sum"], 1, True), f(c["mean"], 2, True), f(c["sharpe"], 2, True), f(c["lo90"], 2, True), f(c["hi90"], 2, True),
        " | ".join(f(m[1], 0, True) + (" (мало)" if c["months_few"][i] else "") for i, m in enumerate(c["months"])), f(c["rr"]["rr"]), f(st["mean_loss_pct"], 1) if st["n"] else "нет стопов", f(c["maxdd"], 1)) \
        + ((" %s |" % ov_txt(c)) if with_ov else "")


def ladder_table(R, CN, u, s, fams):
    b = R[(u, s)]["base"]
    cols = "| ступень | сделок | Σ $ | $ на сделку | Шарп | 90 % инт. $/сд. | " + " | ".join("Σ " + m for m in MONTHS) + " | R:R | стоп, % маржи | макс. просадка, $ |" + (" J с `L_n24` (доля сделок клетки в `L_n24`) |" if s == "L" else "")
    lines = [cols, "|---|" + "---:|" * (len(cols.split("|")) - 3)]
    lines.append(row_cells("**база (без фильтра)**", b, with_ov=s == "L"))
    for c in CN[(u, s)]["ladders"]:
        if c["fam"] in fams:
            lines.append(row_cells(label(c), R[(u, s)]["ladders"][c["cid"]], with_ov=s == "L"))
    return "\n".join(lines)


def all_cells(R, R8, u, s):
    """Все клетки TK-638 + TK-639 стороны: [(источник, cid, ячейка)] — для шумового эталона по всей просмотренной сетке."""
    out = [("TK-639", k, c) for k, c in R[(u, s)]["ladders"].items()]
    out += [("TK-638", k, c) for k, c in R8[(u, s)]["pool"].items()] + [("TK-638", k, c) for k, c in R8[(u, s)]["ladders"].items()]
    return out


def neighbours(CN, u, s, cid):
    """Соседи клетки: соседние ступени того же ряда и парная ступень «≥ / ≤» того же порога."""
    cs = CN[(u, s)]["ladders"]
    me = next(c for c in cs if c["cid"] == cid)
    out = []
    for c in cs:
        if c["fam"] != me["fam"] or c["cid"] == cid:
            continue
        if c["dir"] == me["dir"] and abs(round(c["ps"][0] * 100) - round(me["ps"][0] * 100)) == 20:
            out.append(("соседняя ступень", c))
        elif c["dir"] != me["dir"] and c["ps"][0] == me["ps"][0]:
            out.append(("парная ступень (другое направление, тот же порог)", c))
    return out


def build():
    R, CN, R8, thr, gate = load()
    md = []
    md.append("# П-42 §13.37 (TK-639): число сделок за 6 и 12 ч и средний размер сделки — 20.11.2026\n")
    bh = {s: R[("h775", s)]["base"] for s in "LS"}
    md.append("**R:R и убыток стопа (измерено; ставка 500 $, плечо 2, маржа 250 $):** база h775 — лонг R:R %s, средний убыток стопа %s $ = %s %% маржи; шорт R:R %s, %s $ = %s %% маржи. Для каждой клетки колонки R:R и «стоп, %% маржи» — в таблицах ниже.\n" % (
        f(bh["L"]["rr"]["rr"]), f(bh["L"]["rr"]["stops"]["mean_loss"]), f(-bh["L"]["rr"]["stops"]["mean_loss_pct"], 1), f(bh["S"]["rr"]["rr"]), f(bh["S"]["rr"]["stops"]["mean_loss"]), f(-bh["S"]["rr"]["stops"]["mean_loss_pct"], 1)))
    md.append("@@ANSWER@@\n")
    md.append("## Ворота и порядок\n")
    g = gate["unis"]
    md.append("Протокол §13.37 записан до счёта (`f482e7a3b`), пороги и ворота п. 11 — до P&L, критик `thr_sia32k2tr5` — PASS по п. 11 до первого чтения P&L; движок прогонялся юнитом `tk639-pnl` один раз (srv). "
              "**G-n39** (Rust `tsize` = независимый пересчёт по zip: h775 %d, o566 %d кандидатов, расхождений %d / %d, максимальное относительное расхождение оборота %.1e), **G-reg** (`n60` и `n1440` = `n60` и `n24` TK-638 на всех строках: %d + %d, расхождений 0), "
              "**G8** (`no_key = 0`, `dropped` = пересчёту во всех 96 прогонах лестниц на каждой вселенной) — пройдены. Данные: окна wa / wb / wc, 01.07–13.11 (136 суток), комиссия по тарифу владельца, числа — **измерено**; код `tools/p42/tk639_{feat,run,post,report}.py`, бинарь `alpha-feat` md5 `44ca3843` (подкоманда `tsize`).\n" % (
                  g["h775"]["checked"], g["o566"]["checked"], g["h775"]["mismatches"], g["o566"]["mismatches"], max(g["h775"]["max_rel_v"], g["o566"]["max_rel_v"]), g["h775"]["reg"]["rows"], g["o566"]["reg"]["rows"]))
    md.append("## Пороги (абсолютные, одно число на все монеты; общий квантиль по сигналам базы h775 стороны в зачётных диапазонах трёх окон, 2 значащие цифры; те же числа на o566)\n")
    for s in "LS":
        t = thr[s]
        lines = ["| величина (%s) | q20 | q40 | q60 | q80 |" % SIDE[s], "|---|---:|---:|---:|---:|"]
        for fam, txt in (("NC360", "число сделок за 6 ч"), ("NC720", "число сделок за 12 ч"), ("SZ60", "размер сделки за 1 ч, $"), ("SZ360", "размер сделки за 6 ч, $"), ("SZ1440", "размер сделки за 24 ч, $")):
            lines.append("| %s | %s |" % (txt, " | ".join(("{:,}".format(int(t[fam]["q"][k])).replace(",", " ") if t[fam]["q"][k] >= 100 else ("%g" % t[fam]["q"][k]).replace(".", ",")) for k in ("0.20", "0.40", "0.60", "0.80"))))
        md.append("\n".join(lines) + "\n")
    for u in ("h775", "o566"):
        md.append("## %s — %s\n" % ("Решающая вселенная" if u == "h775" else "Справочно", UNI[u]))
        for s in "LS":
            b = R[(u, s)]["base"]
            md.append("### %s: %s (база: %d сделок, Σ %s $, Шарп %s, R:R %s)\n" % (u, SIDE[s], b["n"], f(b["sum"], 1, True), f(b["sharpe"]), f(b["rr"]["rr"])))
            for key, title, fams in FAMS:
                md.append("**%s — %s, %s**\n\n%s\n" % (title, u, SIDE[s], ladder_table(R, CN, u, s, fams)))
    md.append("## Шумовой эталон и соседи (§13.37 п. 9)\n")
    nz = {}
    for s in "LS":
        cells = all_cells(R, R8, "h775", s)
        sharpes = [c["sharpe"] for _, _, c in cells]
        best_all = max(cells, key=lambda x: x[2]["sharpe"])
        mine = [x for x in cells if x[0] == "TK-639"]
        best_m = max(mine, key=lambda x: x[2]["sharpe"])
        se_m, nz_m = noise(best_m[2]["sharpe"])
        se_a, nz_a = noise(best_all[2]["sharpe"])
        nz[s] = dict(best_all=best_all, best_m=best_m, nz_m=nz_m, nz_a=nz_a, se_m=se_m, se_a=se_a, n_cells=len(cells))
        md.append("**%s, h775.** Просмотрено %d клетки на сторону (TK-638: %d; TK-639: %d; клетки перекрываются). Наибольший Шарп среди клеток TK-639 — `%s` (%s: n = %d, Σ %s $, Шарп %s, SE %s): шум по %d испытаниям достигает %s, дефлированный %s (t %s), «переживает 5 %%»: %s. "
                  "Наибольший Шарп среди всех %d клеток — `%s` (%s): Шарп %s, дефлированный %s. **Найдено на этих данных, отбор из %d клеток, прирост по построению; не результат.**\n" % (
                      SIDE[s], len(cells), N_TK638, N_TK639, best_m[1], label(next(c for c in CN[("h775", s)]["ladders"] if c["cid"] == best_m[1])), best_m[2]["n"], f(best_m[2]["sum"], 0, True), f(best_m[2]["sharpe"]), f(se_m), N_ALL,
                      f(nz_m["noise_bar"]), f(nz_m["deflated"]), f(nz_m["t_deflated"]), "да" if nz_m["survives"] else "нет", len(cells), best_all[1], best_all[0], f(best_all[2]["sharpe"]), f(nz_a["deflated"]), len(cells)))
        lines = ["| соседи `%s` | сделок | Σ $ | $ на сделку | Шарп |" % best_m[1], "|---|---:|---:|---:|---:|"]
        b = R[("h775", s)]["base"]
        lines.append("| база | %d | %s | %s | %s |" % (b["n"], f(b["sum"], 1, True), f(b["mean"], 2, True), f(b["sharpe"], 2, True)))
        lines.append("| сама клетка | %d | %s | %s | %s |" % (best_m[2]["n"], f(best_m[2]["sum"], 1, True), f(best_m[2]["mean"], 2, True), f(best_m[2]["sharpe"], 2, True)))
        for kind, c in neighbours(CN, "h775", s, best_m[1]):
            cc = R[("h775", s)]["ladders"][c["cid"]]
            lines.append("| %s: %s | %d | %s | %s | %s |" % (kind, label(c), cc["n"], f(cc["sum"], 1, True), f(cc["mean"], 2, True), f(cc["sharpe"], 2, True)))
        md.append("\n".join(lines) + "\n")
        if s == "L":
            o = best_m[2]["ln24"]
            md.append("**Пересечение с замороженным `L_n24` (условие критика 20.11: клетка «вперёд» не должна быть копией `L_n24`).** Клетка `%s`: %d сделок, `L_n24` — %d сделок, общих %d; Jaccard %s; в `L_n24` лежит %s %% сделок клетки, в клетке — %s %% сделок `L_n24`. Колонка «J с `L_n24`» есть у всех лонговых клеток выше и в приложении.\n" % (
                best_m[1], o["cell"], o["ref"], o["inter"], f(o["jaccard"], 2), f(100 * o["share_cell_in_ref"], 0), f(100 * o["share_ref_in_cell"], 0)))
    md.append("@@TAIL@@\n")
    md.append("Файлы: `docs/findings/p42-tradecount-size-2026-11-20/{res,gates}/` (счётные JSON), `cells-all.md` (все клетки, все колонки), srv `/data/p42-tk639/`.\n")
    return "\n".join(md), R, CN, nz


def appendix(R, CN):
    out = ["# Приложение: все клетки (TK-639, §13.37)\n", "Колонки: сделок; Σ $; $ на сделку; Шарп (год); 90 % интервал $/сд. (бутстреп по суткам, блок 4); Σ $ по месяцам (июль, август, сентябрь, октябрь, 01–13.11; «м» — месяц с n < 20); макс. просадка, $ (и % маржи 250 $); "
           "KPI В-120 (дней под водой: самый долгий / p90); R:R; средний убыток стопа, $ (% маржи при плече 2), стопов; доля сделок клетки, что есть в базе. «мало» — n < 70.\n"]
    head = "| клетка | сделок | Σ $ | $/сд. | Шарп | 90 % инт. | Σ по месяцам | макс. просадка | KPI | R:R | стоп | в базе | J с `L_n24` |"
    sep = "|---|---:|---:|---:|---:|---|---|---:|---|---:|---:|---:|---:|"

    def row(name, c):
        st = c["rr"]["stops"]
        return "| %s | %d%s | %s | %s | %s | %s…%s | %s | %s (%s %%) | %s / %s | %s | %s (%s %%), %d | %s |" % (
            name, c["n"], " мало" if c["few"] else "", f(c["sum"], 1, True), f(c["mean"], 2, True), f(c["sharpe"], 2, True), f(c["lo90"], 2, True), f(c["hi90"], 2, True),
            " / ".join(f(m[1], 0, True) + ("м" if c["months_few"][i] else "") for i, m in enumerate(c["months"])), f(c["maxdd"], 1), f(c["maxdd_pct_margin"], 0), c["kpi"]["longest"], f(c["kpi"]["p90"], 0), f(c["rr"]["rr"]),
            f(st["mean_loss"], 2) if st["n"] else "—", f(st["mean_loss_pct"], 1) if st["n"] else "—", st["n"], f(100 * c["in_base_share"], 0) + " %" if "in_base_share" in c else "—") + " %s |" % ov_txt(c)
    for u in ("h775", "o566"):
        for s in "LS":
            out.append("## %s %s\n" % (u, SIDE[s]))
            out.append(head + "\n" + sep)
            out.append(row("**база**", R[(u, s)]["base"]))
            for c in CN[(u, s)]["ladders"]:
                out.append(row(c["cid"] + " — " + label(c), R[(u, s)]["ladders"][c["cid"]]))
            out.append("")
    return "\n".join(out)


def main():
    text, R, CN, nz = build()
    sub = os.path.join(OUT, "p42-tradecount-size-2026-11-20")
    os.makedirs(sub, exist_ok=True)
    narrative = os.path.join(HERE, "tk639_answer.py")
    if os.path.exists(narrative):
        sys.path.insert(0, HERE)
        import tk639_answer as ans  # noqa: E402
        a, tail = ans.answer(R, CN, nz, f, label)
        text = text.replace("@@ANSWER@@", a).replace("@@TAIL@@", tail)
    open(os.path.join(OUT, "p42-tradecount-size-2026-11-20.md"), "w", encoding="utf-8", newline="\n").write(text)
    open(os.path.join(sub, "cells-all.md"), "w", encoding="utf-8", newline="\n").write(appendix(R, CN))
    print("ok: отчёт %d строк" % text.count("\n"))


if __name__ == "__main__":
    main()
