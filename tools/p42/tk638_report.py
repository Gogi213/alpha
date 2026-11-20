#!/usr/bin/env python3
"""П-42 §13.36 (TK-638): отчёт и приложение по результатам юнита tk638-pnl.
  python3 tk638_report.py <каталог res с cells38-*.json, context38-*.json, counts38-*.json> <каталог вывода>
Пишет <вывод>/p42-turnover-grid-2026-11-20.md (отчёт) и <вывод>/p42-turnover-grid-2026-11-20/cells-all.md (все клетки со всеми колонками).
Доллары — ставка 500 $ на сделку; Sharpe — годовой по суточному ряду Σ $ за 136 суток; без бп, без медиан."""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", ".claude", "skills", "alpha-research", "scripts"))
import selection_bias as sb  # noqa: E402

RES, OUT = sys.argv[1:3]
D_LEVELS = (12, 25, 50, 100, 200, 300, 500)
H_LEVELS = (0.25, 0.35, 0.5, 0.7, 1.0, 1.4, 2.0, 3.0, 5.0)
MONTHS = ("июль", "август", "сентябрь", "октябрь", "01–13.11")
SIDE = {"L": "лонг", "S": "шорт"}
UNI = {"h775": "h775 (честная вселенная, 775 контрактов; база с `t24 = 12`)", "o566": "o566 (прежние 566 монет; в базе нет ключа D)"}
MIN_TRADES = 70
T_DAYS = 136
N_VIEWED = 71


def f(x, d=2, sign=False):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    s = ("%.*f" % (d, abs(x))).replace(".", ",")
    neg = x < 0 and float(s.replace(",", ".")) != 0
    return ("−" if neg else ("+" if sign and float(s.replace(",", ".")) != 0 else "")) + s


def hh(h):
    return ("%g" % h).replace(".", ",")


def cid(d, h):
    return "D%g_H%g" % (d, h)


def load():
    R = {}
    for u in ("h775", "o566"):
        for s in "LS":
            R[(u, s)] = json.load(open(os.path.join(RES, "cells38-%s-%s.json" % (u, s)), encoding="utf-8"))
    C = {u: json.load(open(os.path.join(RES, "context38-%s.json" % u), encoding="utf-8")) for u in ("h775", "o566")}
    CN = {(u, s): json.load(open(os.path.join(RES, "counts38-%s-%s.json" % (u, s)), encoding="utf-8")) for u in ("h775", "o566") for s in "LS"}
    thr = json.load(open(os.path.join(RES, "thr38.json"), encoding="utf-8"))
    return R, C, CN, thr


def rows_of(u):
    return [d for d in D_LEVELS] + (["none"] if u == "o566" else [])


def cell(R, u, s, d, h):
    return R[(u, s)]["pool"]["D%s_H%g" % (d, h) if d == "none" else cid(d, h)]


def grid(R, u, s, getter, fmt, mark_few=True):
    head = "| D \\ H, млн $ | " + " | ".join(hh(h) for h in H_LEVELS) + " |"
    sep = "|---|" + "---:|" * len(H_LEVELS)
    lines = [head, sep]
    for d in rows_of(u):
        cs = []
        for h in H_LEVELS:
            c = cell(R, u, s, d, h)
            cs.append(fmt(getter(c)) + ("\\*" if mark_few and c["few"] else ""))
        lines.append("| %s | %s |" % ("без D" if d == "none" else d, " | ".join(cs)))
    return "\n".join(lines)


def ladder_label(c):
    return "%s ≥ %s сделок за %s (q%s)" % (c["lad"], "{:,}".format(int(c["N"])).replace(",", " "), "60 мин" if c["lad"] == "N60" else "24 ч", "/".join("%d" % round(p * 100) for p in c["ps"]))


def ladder_table(R, CN, u, s):
    b = R[(u, s)]["base"]
    cols = "| ступень | сделок | Σ $ | $ на сделку | Шарп | 90 % инт. $/сд. | " + " | ".join("Σ " + m for m in MONTHS) + " | R:R | стоп, % маржи | макс. просадка, $ |"
    lines = [cols, "|---|" + "---:|" * (len(cols.split("|")) - 3)]

    def row(name, c):
        st = c["rr"]["stops"]
        return "| %s | %d%s | %s | %s | %s | %s…%s | %s | %s | %s | %s |" % (
            name, c["n"], " мало" if c["few"] else "", f(c["sum"], 1, True), f(c["mean"], 2, True), f(c["sharpe"], 2, True), f(c["lo90"], 2, True), f(c["hi90"], 2, True),
            " | ".join(f(m[1], 0, True) + (" (мало)" if c["months_few"][i] else "") for i, m in enumerate(c["months"])), f(c["rr"]["rr"]), f(st["mean_loss_pct"], 1) if st["n"] else "нет стопов", f(c["maxdd"], 1))
    lines.append(row("**база (без фильтра)**", b))
    byc = {x["cid"]: x for x in CN[(u, s)]["ladders"]}
    for k, c in R[(u, s)]["ladders"].items():
        lines.append(row(ladder_label(byc[k]), c))
    return "\n".join(lines)


def noise(sharpe):
    se = math.sqrt(365.0 / T_DAYS * (1 + sharpe * sharpe / 730.0))
    r = sb.significance(sharpe, se, N_VIEWED)
    return se, r


def build():
    R, C, CN, thr = load()
    g = lambda u, s: R[(u, s)]  # noqa: E731
    bh = {s: g("h775", s)["base"] for s in "LS"}
    bo = {s: g("o566", s)["base"] for s in "LS"}

    def rr_range(u, s):
        v = [(cell(R, u, s, d, h)["rr"]["rr"], d, h) for d in D_LEVELS for h in H_LEVELS if not math.isnan(cell(R, u, s, d, h)["rr"]["rr"])]
        return min(v), max(v)

    def stop_range(u, s):
        v = [cell(R, u, s, d, h)["rr"]["stops"]["mean_loss_pct"] for d in D_LEVELS for h in H_LEVELS if cell(R, u, s, d, h)["rr"]["stops"]["n"]]
        return min(v), max(v)

    lo_l, hi_l = rr_range("h775", "L")
    lo_s, hi_s = rr_range("h775", "S")
    sl, sh_l = stop_range("h775", "L")
    ss, sh_s = stop_range("h775", "S")
    first = ("**R:R и убыток стопа (измерено; ставка 500 $, плечо 2, маржа 250 $):** база h775 — лонг R:R %s, средний убыток стопа %s $ = %s %% маржи; шорт R:R %s, %s $ = %s %% маржи. "
             "По 63 клеткам сетки h775: лонг R:R %s…%s (ниже 1 при D ≥ 200 и при H = 5 на D = 12…25), убыток стопа %s…%s %% маржи; шорт R:R %s…%s (растёт с D), убыток стопа %s…%s %% маржи "
             "(при D ≥ 200 стопов у шорта нет). Полная таблица по каждой клетке — в приложении.") % (
        f(bh["L"]["rr"]["rr"]), f(bh["L"]["rr"]["stops"]["mean_loss"]), f(-bh["L"]["rr"]["stops"]["mean_loss_pct"], 1), f(bh["S"]["rr"]["rr"]), f(bh["S"]["rr"]["stops"]["mean_loss"]), f(-bh["S"]["rr"]["stops"]["mean_loss_pct"], 1),
        f(lo_l[0]), f(hi_l[0]), f(-sh_l, 1), f(-sl, 1), f(lo_s[0]), f(hi_s[0]), f(-sh_s, 1), f(-ss, 1))

    lad = {s: g("h775", s)["ladders"] for s in "LS"}
    best_l, best_s = lad["L"]["N24_q60"], lad["S"]["N24_q20"]
    se_l, nz_l = noise(best_l["sharpe"])
    se_s, nz_s = noise(best_s["sharpe"])
    ctx = C["h775"]
    ov = ctx["L"]["overlap"]["N24_q60|D25_H0.5"]
    ov50 = ctx["L"]["overlap"]["N24_q60|D50_H0.5"]

    md = []
    md.append("# П-42 §13.36 (TK-638): титровка оборота D × H и числа сделок — 20.11.2026\n")
    md.append(first + "\n")
    md.append("**Ответ (описательно, без вердикта «работает / не работает»; 0 испытаний Холма):** "
              "(1) суточный оборот выше базового 12 млн $ — **лонг**: сумма падает с %s $ (428 сделок, Шарп %s) до %s $ при D = 50 и %s $ при D = 200 (%d сделок); $ на сделку максимальна при D = 25 (%s $ против %s $ у базы), Шарп при D = 25 тот же (%s). "
              "**Шорт** положителен во всех 63 клетках: Σ %s…%s $, Шарп %s…%s; сумма падает с ростом D и H, $ на сделку при D ≤ 100 — 0,5–0,8 $ (D ≥ 200 — 0,8–1,3 $, но это 27–62 сделки, «мало»). "
              "(2) часовой оборот: на h775 клетки H < 0,5 почти ничего не добавляют (при H = 0,25 лишь +2 лонг- и +7 шорт-сделок: ключ D = 12 уже отсёк слабые монеты); H ≥ 3 для лонга — ноль или минус (D = 12: %s $ при H = 3, %s $ при H = 5). "
              "Без ключа D (o566) часовой оборот работает как единственный фильтр: лонг %s $ при H = 0,25, %s $ при H = 0,7, %s $ при H = 5 (база %s $). "
              "(3) число сделок монеты за 24 ч **заметнее оборота**: лонг h775, N24 ≥ 220 000 сделок за сутки — n = %d, **%s $**, Шарп %s, во всех пяти месяцах плюс (база %s $, Шарп %s) — **найдено на этих данных** (отбор из %d клетки на сторону, прирост по построению), см. шумовой эталон ниже; "
              "число сделок за 60 мин (N60) лонг базу по сумме не превышает ни на одной ступени (q40: %s $; q60 и q80 ≈ 0). Шорт: N24 ≥ 78 000 — %s $ (Шарп %s) против базы %s $ (Шарп %s); выше этого порога сумма ниже базы.\n" % (
                  f(bh["L"]["sum"], 0, True), f(bh["L"]["sharpe"]), f(cell(R, "h775", "L", 50, 0.5)["sum"], 0, True), f(cell(R, "h775", "L", 200, 0.5)["sum"], 0, True), cell(R, "h775", "L", 200, 0.5)["n"],
                  f(cell(R, "h775", "L", 25, 0.5)["mean"], 2), f(bh["L"]["mean"], 2), f(cell(R, "h775", "L", 25, 0.5)["sharpe"]),
                  f(min(cell(R, "h775", "S", d, h)["sum"] for d in D_LEVELS for h in H_LEVELS), 0), f(max(cell(R, "h775", "S", d, h)["sum"] for d in D_LEVELS for h in H_LEVELS), 0),
                  f(min(cell(R, "h775", "S", d, h)["sharpe"] for d in D_LEVELS for h in H_LEVELS), 1), f(max(cell(R, "h775", "S", d, h)["sharpe"] for d in D_LEVELS for h in H_LEVELS), 1),
                  f(cell(R, "h775", "L", 12, 3.0)["sum"], 1, True), f(cell(R, "h775", "L", 12, 5.0)["sum"], 0, True),
                  f(cell(R, "o566", "L", "none", 0.25)["sum"], 0, True), f(cell(R, "o566", "L", "none", 0.7)["sum"], 0, True), f(cell(R, "o566", "L", "none", 5.0)["sum"], 0, True), f(bo["L"]["sum"], 0, True),
                  best_l["n"], f(best_l["sum"], 0, True), f(best_l["sharpe"]), f(bh["L"]["sum"], 0, True), f(bh["L"]["sharpe"]), N_VIEWED,
                  f(lad["L"]["N60_q40"]["sum"], 0, True), f(best_s["sum"], 0, True), f(best_s["sharpe"]), f(bh["S"]["sum"], 0, True), f(bh["S"]["sharpe"])))
    md.append("## Ворота и порядок\n")
    md.append("Протокол §13.36 записан до счёта (`e5055f44e`), пороги и ворота п. 11 — до P&L (`c2f20dd71`), критик `thr_sia32k2tr5` — PASS по п. 11 до первого чтения P&L; движок прогонялся юнитом `tk638-pnl` один раз (srv). "
              "**G-n** (Rust `tcount` = независимый пересчёт по zip, 298 кандидатов, 0 расхождений), **G-turn** (0 нарушений ключей базы), **G8** (`no_key = 0`, `dropped` = пересчёту во всех 48 прогонах лестниц на каждой вселенной), "
              "**G-base** (клетка `D12_H0,5` = база h775 по списку и P&L на обеих сторонах и всех окнах; на o566 — `без D, H = 0,5` = база o566) — пройдены. "
              "**G-pool** (доля сделок клетки, что есть среди сделок базы, D ≥ 25, H ≥ 0,5): 94–99 % — клетки пула почти подмножества базы, отличие от пост-фильтра TK-636 (`TOV`) — единицы процентов сделок. "
              "Данные: окна wa / wb / wc, 01.07–13.11 (136 суток), комиссия по тарифу владельца (§13.35), числа — **измерено**; код `tools/p42/tk638_{feat,run,post,report}.py`, бинарь `alpha-feat` md5 `69fcf7cd`.\n")
    for u in ("h775", "o566"):
        md.append("## %s — %s\n" % ("Решающая вселенная" if u == "h775" else "Справочно", UNI[u]))
        for s in "LS":
            b = g(u, s)["base"]
            md.append("### %s: %s (база: %d сделок, Σ %s $, Шарп %s, R:R %s)\n" % (u, SIDE[s], b["n"], f(b["sum"], 1, True), f(b["sharpe"]), f(b["rr"]["rr"])))
            for title, getter, fmt in (("Σ $ (500 $ на сделку)", lambda c: c["sum"], lambda x: f(x, 0, True)), ("$ на сделку (среднее, без медиан)", lambda c: c["mean"], lambda x: f(x, 2, True)),
                                       ("Шарп (годовой, суточный ряд)", lambda c: c["sharpe"], lambda x: f(x, 2, True)), ("Число сделок", lambda c: c["n"], lambda x: "%d" % x),
                                       ("R:R", lambda c: c["rr"]["rr"], lambda x: f(x, 2))):
                md.append("**%s**\n\n%s\n" % (title, grid(R, u, s, getter, fmt)))
        md.append("\\* — меньше %d сделок за всё окно («мало сделок»); клетки с H < 0,5 расширяют пул базы. Строка «без D» (только o566) — база o566 без ключа `t24` с часовым оборотом H.\n" % MIN_TRADES)
        for s in "LS":
            md.append("**Лестницы числа сделок — %s, %s** (один абсолютный порог на все монеты; общий квантиль по сигналам базы h775 в зачётных диапазонах; те же `N` на o566)\n\n%s\n" % (u, SIDE[s], ladder_table(R, CN, u, s)))
    md.append("## Как читать и что не следует\n")
    md.append("Правило п. 8: смотрим области (строки / столбцы сетки, соседи того же знака), а не лучшую клетку; все 63 + 8 клеток на сторону показаны. Эта сетка — описание «где деньги на какой ликвидности», не отбор: **испытаний для Холма 0**, "
              "«просмотрено» (контекст): %d клетка на сторону на h775 и столько же на o566 (одни и те же сигналы базы; клетки перекрываются), плюс прежнее по обороту — TK-636 `TOV` (5 ступеней на сторону) и §13.21 (`t1h` ∈ {0,25…5}, `t24` ∈ {100; 500}).\n" % N_VIEWED)
    md.append("**Число сделок и оборот — связаны, но не одно и то же** (Спирмен на кандидатах зачётных диапазонов, h775): `n24 ~ turn24h` %s (лонг) / %s (шорт), `n60 ~ turn1h` %s / %s. "
              "Лонг, N24 ≥ 220 000 против клетки D = 25, H = 0,5: общих %d сделок (Σ %s $), только по числу сделок — %d (Σ %s $), только по обороту — %d (Σ %s $); против D = 50: общих %d (Σ %s $), только по числу сделок %d (Σ %s $), только по обороту %d (Σ %s $). "
              "У шорта сделки «только по обороту» в плюсе (+84,6 $ / +49,3 $ при сравнении с D = 25 / 50) — картина другая.\n" % (
                  f(ctx["L"]["rho_n24_turn24h"], 2), f(ctx["S"]["rho_n24_turn24h"], 2), f(ctx["L"]["rho_n60_turn1h"], 2), f(ctx["S"]["rho_n60_turn1h"], 2),
                  ov["both"], f(ov["sum_both"], 1, True), ov["only_ladder"], f(ov["sum_only_ladder"], 1, True), ov["only_turn"], f(ov["sum_only_turn"], 1, True),
                  ov50["both"], f(ov50["sum_both"], 1, True), ov50["only_ladder"], f(ov50["sum_only_ladder"], 1, True), ov50["only_turn"], f(ov50["sum_only_turn"], 1, True)))
    md.append("**Шумовой эталон (по сетке, из которой смотрели; `selection_bias.py`, %d клетка на сторону; SE Шарпа — по формуле для 136 независимых суток, без поправки на автокорреляцию и толстые хвосты):** лонг N24 ≥ 220 000: Шарп %s, SE %s — шум по %d независимым испытаниям достигает %s, "
              "дефлированный Шарп %s (t %s), «переживает 5 %%»: %s. Шорт N24 ≥ 78 000: Шарп %s, SE %s — шум %s, дефлированный %s (t %s), «переживает 5 %%»: %s. Клетки перекрываются (одни сигналы): независимых испытаний меньше %d, значит планка шума завышена, а SE занижен (допущения выше); "
              "прирост получен по построению отбора, **это кандидат для проверки вперёд, «найдено на этих данных»**, а не результат. "
              "Плато лонг N24: все четыре ступени (q20…q80) лучше базы по Шарпу (%s…%s против %s) и по $ на сделку (%s…%s против %s); по сумме выше базы три из четырёх (q80: %s $). Плато шорт N24: две из четырёх (q20, q40) выше базы по Шарпу, остальные ниже. "
              "На o566 (те же `N`) лонг N24: %s / %s / %s / %s $ против базы %s $ — повторяется.\n" % (
                  N_VIEWED, f(best_l["sharpe"], 2), f(se_l, 2), N_VIEWED, f(nz_l["noise_bar"], 2), f(nz_l["deflated"], 2), f(nz_l["t_deflated"], 2), ("да" if nz_l["survives"] else "нет"),
                  f(best_s["sharpe"], 2), f(se_s, 2), f(nz_s["noise_bar"], 2), f(nz_s["deflated"], 2), f(nz_s["t_deflated"], 2), ("да" if nz_s["survives"] else "нет"), N_VIEWED,
                  f(min(c["sharpe"] for k, c in lad["L"].items() if k.startswith("N24")), 1), f(max(c["sharpe"] for k, c in lad["L"].items() if k.startswith("N24")), 1), f(bh["L"]["sharpe"]),
                  f(min(c["mean"] for k, c in lad["L"].items() if k.startswith("N24")), 2), f(max(c["mean"] for k, c in lad["L"].items() if k.startswith("N24")), 2), f(bh["L"]["mean"], 2), f(lad["L"]["N24_q80"]["sum"], 0, True),
                  *[f(g("o566", "L")["ladders"]["N24_q%d" % q]["sum"], 0, True) for q in (20, 40, 60, 80)], f(bo["L"]["sum"], 0, True)))
    md.append("Ограничения: 136 суток, одна вселенная в решающей роли; месячные ряды короткие (01–13.11 — 13 суток); часть клеток — «мало сделок» (шорт N60_q80 и N24_q80 — 63 / 67 сделок; D ≥ 200 — ≤ 70); "
              "клетки сетки и ступени лестниц сильно перекрываются; поправки на множественные испытания нет (описательная сетка). Любое продолжение (заморозка вперёд, форма признака) — только записью координатора и условиями критика (§13.36 п. 9).\n")
    md.append("Файлы: `docs/findings/p42-turnover-grid-2026-11-20/{res,gates}/` (счётные JSON), `cells-all.md` (все клетки, все колонки), srv `/data/p42-tk638/`.\n")
    return "\n".join(md), R, CN


def appendix(R, CN):
    out = ["# Приложение: все клетки (TK-638, §13.36)\n", "Колонки: сделок; Σ $; $ на сделку; Шарп (год); 90 % интервал $/сд. (бутстреп по суткам, блок 4); Σ $ по месяцам (июль, август, сентябрь, октябрь, 01–13.11; «м» — месяц с n < 20); макс. просадка, $ (и % маржи 250 $); "
           "KPI В-120 (дней под водой: самый долгий / p90); R:R; средний убыток стопа, $ (% маржи при плече 2), стопов; доля сделок клетки, что есть в базе. «мало» — n < 70.\n"]
    head = "| клетка | сделок | Σ $ | $/сд. | Шарп | 90 % инт. | Σ по месяцам | макс. просадка | KPI | R:R | стоп | в базе |"
    sep = "|---|---:|---:|---:|---:|---|---|---:|---|---:|---:|---:|"

    def row(name, c):
        st = c["rr"]["stops"]
        return "| %s | %d%s | %s | %s | %s | %s…%s | %s | %s (%s %%) | %s / %s | %s | %s (%s %%), %d | %s |" % (
            name, c["n"], " мало" if c["few"] else "", f(c["sum"], 1, True), f(c["mean"], 2, True), f(c["sharpe"], 2, True), f(c["lo90"], 2, True), f(c["hi90"], 2, True),
            " / ".join(f(m[1], 0, True) + ("м" if c["months_few"][i] else "") for i, m in enumerate(c["months"])), f(c["maxdd"], 1), f(c["maxdd_pct_margin"], 0), c["kpi"]["longest"], f(c["kpi"]["p90"], 0), f(c["rr"]["rr"]),
            f(st["mean_loss"], 2) if st["n"] else "—", f(st["mean_loss_pct"], 1) if st["n"] else "—", st["n"], f(100 * c["in_base_share"], 0) + " %" if "in_base_share" in c else "—")
    for u in ("h775", "o566"):
        for s in "LS":
            out.append("## %s %s\n" % (u, SIDE[s]))
            out.append(head + "\n" + sep)
            out.append(row("**база**", R[(u, s)]["base"]))
            for k, c in R[(u, s)]["pool"].items():
                out.append(row(k, c))
            byc = {x["cid"]: x for x in CN[(u, s)]["ladders"]}
            for k, c in R[(u, s)]["ladders"].items():
                out.append(row(ladder_label(byc[k]), c))
            out.append("")
    return "\n".join(out)


def main():
    text, R, CN = build()
    os.makedirs(os.path.join(OUT, "p42-turnover-grid-2026-11-20"), exist_ok=True)
    open(os.path.join(OUT, "p42-turnover-grid-2026-11-20.md"), "w", encoding="utf-8", newline="\n").write(text)
    open(os.path.join(OUT, "p42-turnover-grid-2026-11-20", "cells-all.md"), "w", encoding="utf-8", newline="\n").write(appendix(R, CN))
    print("ok: отчёт %d строк" % text.count("\n"))


if __name__ == "__main__":
    main()
