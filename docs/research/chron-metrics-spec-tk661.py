"""Опись TK-661 → chron-metrics-spec-tk661.json: числа записей летописи с итогом, которых не взял общий разбор TK-647.

Каждая строка — либо выборка из файла результатов в git (pick: путь JSON / фильтр CSV → поля), либо число, переписанное
из текста записи («из текста», источник — строка снимка `docs/archive/chronicle-2026-11-20/`), либо пометка «нет чисел»
с причиной. Ничего не досчитывается (п. 9): $ / сделку = Σ / n только там, где обе величины напечатаны.
Ключи extra: bps_point / bps_lower — «точка / нижняя» bps на круг; pnl_usd_1000 — колонка «P&L $1000» реестра E;
d_usd — Δ $ к базе пересчёта; account_usd / account_pct — счёт 2500 $; прочие — как в тексте.
Запуск: python docs/research/chron-metrics-spec-tk661.py && python tools/chron.py metrics-spec docs/research/chron-metrics-spec-tk661.json
"""
import json
from pathlib import Path

E = "docs/archive/chronicle-2026-11-20/EXPERIMENTS.md"
J = "docs/archive/chronicle-2026-11-20/JOURNAL.md"
items = []


def text(entry, src, line, rows, **kw):
    items.append(dict(entry=entry, src=src, line=line, text=1, rows=rows, **kw))


def gap(entry, reason, note, src=None, line=None):
    items.append(dict(entry=entry, gap=reason, note=note, **({"src": src, "line": line} if src else {})))


def pick(entry, src, picks, **kw):
    items.append(dict(entry=entry, src=src, pick=picks, **kw))


def r(cell, **kw):
    return dict(cell=cell, **kw)


# ── П-36 / пампы (В-108: отчёты удалены, числа — из текста записи)
text(50, J, 98, [r("круглые числа: подтверждено из проверенных", extra={"confirmed": 0, "tested": 8}),
                 r("время суток: подтверждено из найденных", extra={"confirmed": 0, "found": 48}),
                 r("лонг: доля базы на 360 мин", side="long", extra={"base_pct": 74})])
text(53, J, 108, [r("ранняя фаза (воспламенение NATR): lift; месяцев с тем же знаком", extra={"lift_min": 4, "lift_max": 17, "months_pos": 10, "months": 10})])

# ── П-42 §13.18 (TK-592): отложенные сутки, верхняя треть CB3 — таблица отчёта
PC = "docs/findings/p42-pairs-core-2026-11-18.md"
holdout = [r("CB3 верхняя треть, модель A", side="long", line=103, extra={"episodes": 268, "breaks": 25, "share_pct": 9.3, "control_pct": 6.2, "e1_pp": 3.1, "e1_lo": -0.1, "e1_hi": 6.5}),
           r("CB3 верхняя треть, модель B", side="long", line=104, extra={"episodes": 270, "breaks": 25, "share_pct": 9.3, "control_pct": 6.0, "e1_pp": 3.2, "e1_lo": -0.2, "e1_hi": 6.5}),
           r("CB3 верхняя треть, модель A", side="short", line=105, extra={"episodes": 341, "breaks": 27, "share_pct": 7.9, "control_pct": 9.0, "e1_pp": -1.1, "e1_lo": -4.1, "e1_hi": 1.9}),
           r("CB3 верхняя треть, модель B", side="short", line=106, extra={"episodes": 366, "breaks": 24, "share_pct": 6.6, "control_pct": 8.9, "e1_pp": -2.3, "e1_lo": -4.9, "e1_hi": 0.7})]
for eid in (75, 135):
    text(eid, PC, None, holdout, win="14–16.11 (отложенные сутки)")
text(75, J, 151, [r("пары: читаемых клеток / прошли BH q = 0,10", same_data=1, extra={"cells": 1981, "bh_pass": 0}),
                  r("CB3 − логистика, AUC (не больше)", extra={"d_auc_max": 0.013})])
text(135, E, 54, [r("пары: клеток / прошли BH q = 0,10", same_data=1, extra={"cells": 1981, "bh_pass": 0}),
                  r("CB3 − логистика, AUC (не больше)", extra={"d_auc_max": 0.013})])

# ── П-42 §13.20 (TK-594): ΔAUC наборов против S0 — файл; август — текст
pick(77, "docs/findings/p42-flow-deriv-2026-11-18/cmp/delta.csv",
     [dict(where={}, side="{side}", cell="{dir} {set}−S0 {model}", win="09–11 (поиск)",
           extra={"d_auc": "dAUC", "lo975": "lo975", "hi975": "hi975", "d_e1_top": "dE1_top"})])
text(77, J, 155, [r("август (отложенный), ΔAUC S3 − S0, CB3", side="long", win="август", extra={"d_auc": 0.0013, "lo": -0.0055, "hi": 0.0083, "auc_base": 0.633}),
                  r("август (отложенный), ΔAUC S3 − S0, CB3", side="short", win="август", extra={"d_auc": -0.0079, "lo": -0.0147, "hi": -0.0010, "auc_base": 0.688})])

# ── П-42 §13.21: стадия F, расширение, сборка C (A найдено на этих данных; B — описательно)
L83 = [r("Ext_s0.5 (расширение s → 0.5)", side="long", win="A", trades=312, usd=43.56, mean=0.140, same_data=1, extra={"d_a_usd_day": -2.54, "lo": -6.19, "hi": 0.57, "se": 1.73}),
       r("сборка C", side="long", win="A", trades=312, usd=119.79, mean=0.384, verdict="не ясно", same_data=1, extra={"usd_per_day": 3.99, "lo": -4.68, "hi": 12.60}),
       r("сборка C", side="long", win="B", trades=286, usd=66.45, mean=0.232, verdict="не ясно", extra={"usd_per_day": 2.22, "lo": -3.23, "hi": 7.56}),
       r("сборка C", side="short", win="A", trades=223, usd=111.79, mean=0.501, verdict="не ясно", same_data=1, extra={"usd_per_day": 3.73, "lo": 0.05, "hi": 7.37, "holm_lo": -3.75}),
       r("сборка C", side="short", win="B", trades=203, usd=101.07, mean=0.498, verdict="не ясно", extra={"usd_per_day": 3.37, "lo": 0.74, "hi": 5.88, "holm_lo": -1.92}),
       r("простая база", side="long", win="A", trades=141, usd=22.03, mean=0.156),
       r("простая база", side="long", win="B", trades=150, usd=-16.32, mean=-0.109),
       r("простая база", side="short", win="A", trades=99, usd=75.86, mean=0.766),
       r("простая база", side="short", win="B", trades=94, usd=69.63, mean=0.741),
       r("Δ(C − простая база), $/сут.", side="long", win="A", extra={"d_usd_day": 3.26, "lo": -1.28, "hi": 7.83, "se": 2.33}),
       r("Δ(C − простая база), $/сут.", side="long", win="B", extra={"d_usd_day": 2.76, "lo": -1.44, "hi": 7.06, "se": 2.18}),
       r("Δ(C − простая база), $/сут.", side="long", win="60 сут.", extra={"d_usd_day": 3.01, "lo": -0.07, "hi": 6.09}),
       r("Δ(C − простая база), $/сут.", side="short", win="A", extra={"d_usd_day": 1.20, "lo": -1.34, "hi": 3.48, "se": 1.22}),
       r("Δ(C − простая база), $/сут.", side="short", win="B", extra={"d_usd_day": 1.05, "lo": -1.17, "hi": 3.16, "se": 1.11}),
       r("Δ(C − простая база), $/сут.", side="short", win="60 сут.", extra={"d_usd_day": 1.12, "lo": -0.58, "hi": 2.71}),
       r("Δ(C − база стадии P)", side="long", win="A", extra={"d": 0.85}),
       r("Δ(C − база стадии P)", side="long", win="B", extra={"d": 1.10, "lo": -3.06, "hi": 5.71}),
       r("Δ(C − база стадии P)", side="short", win="A", extra={"d": 0.25}),
       r("Δ(C − база стадии P)", side="short", win="B", extra={"d": 1.73, "lo": 0.05, "hi": 3.72, "se": 0.94}),
       r("F: maxmv1_lo_10 (плато нарушено)", side="long", win="A", same_data=1, extra={"d_a": 0.54, "neighbor_d": -2.17}),
       r("F: depth_mean_bp_hi_20 (худшая)", side="long", win="A", same_data=1, extra={"d_a": -4.12}),
       r("F: tpm15_lo_10 (плато нарушено)", side="short", win="A", same_data=1, extra={"d_a": 0.72, "neighbor_d": -0.03}),
       r("F: tpm60_lo_20 (сглаженный tpm60_lo_25 −0,13)", side="short", win="A", same_data=1, extra={"d_a": 0.31, "smoothed_d": -0.13})]
text(83, J, 167, L83)

# ── П-42 §13.26 (TK-604): ступень 1 (ii) и выбор п. 6 — файлы результатов заморозки
S2 = "docs/research/freeze/tk604-results/step1-ii.json"
SEL = "docs/research/freeze/tk604-results/select.json"
S1 = "docs/research/freeze/tk604-results/step1-i.json"
for sd, side in (("L", "long"), ("S", "short")):
    pick(101, S2, [dict(ptr="%s/total" % sd, side=side, cell="база (несфильтрованная)", map={"trades": "n", "usd": "sum", "mean_usd": "mean"}, extra={"sigma": "sigma"}),
                   dict(ptr="%s/by_bk20/*" % sd, side=side, cell="bk20: {0}", map={"trades": "n", "usd": "total", "mean_usd": "mean"}, extra={"pos": "pos"}),
                   dict(ptr="%s/by_mtr/*" % sd, side=side, cell="m_tr: {0}", map={"trades": "n", "usd": "total", "mean_usd": "mean"}, extra={"pos": "pos"})],
         verdict="описательно", win="01.07–13.11 (136 сут.)")
    for eid in (102, 140):
        pick(eid, SEL, [dict(ptr="%s" % sd, side=side, cell="база %s_base" % sd, map={"trades": "n_base", "usd": "sum_base"}),
                        dict(ptr="%s/select/rows/*" % sd, side=side, cell="{cid}", same_data=1, map={"trades": "n_c"},
                             extra={"d_usd_trade": "d", "n_base": "n_r", "n_excluded": "n_ex"}, note="d — Δ $ на сделку (оставшиеся − база), выбор п. 6")],
             win="01.07–13.11 (136 сут.)")
        pick(eid, S1, [dict(ptr="%s/auc_bk" % sd, side=side, cell="AUC bk против y", extra={"auc": "auc", "lo": "lo_d", "hi": "hi_d", "n_episodes": "n"})],
             win="01.07–13.11 (136 сут.)")

# ── П-42 килзоны (TK-622): P&L базы по корзинам — файл (метрика gf = $ на сделку базы)
pick(111, "docs/findings/p42-killzones-2026-11-19/results.csv",
     [dict(where={"metric": "gf"}, side="{side}", cell="{scheme} · {bucket}", map={"trades": "n_in", "usd": "sum_in", "mean_usd": "mean_in"},
           extra={"d_usd_trade": "D", "lo": "lo", "hi": "hi", "p": "p", "p_holm": "p_holm", "n_out": "n_out", "mean_out": "mean_out"})],
     verdict="описательно", win="01.07–13.11")

gap(127, "не запускалось", "черновик протокола П-01 ждёт владельца — счёта нет", E, 46)

# ── П-02: эффекты перечтения R2 по гипотезам и месяцам — файл
pick(128, "docs/archive/findings/p02-r2-read-2026-09-26.json",
     [dict(ptr="*/*", cell="{0}", win="{1}", extra={"effect": "est", "ci_lo": "ci95/0", "ci_hi": "ci95/1", "p_holm": "p_holm", "n_days": "n_days"})],
     same_data=1)

# ── П-10: клетки и базы по месяцам — файл ($ — ставка в файле не названа → extra.usd_raw)
for mon, w in (("aug", "август"), ("sep", "сентябрь")):
    pick(141, "docs/archive/findings/p10-2026-10-02.json",
         [dict(ptr="cells/*", cell="{0}", win=w, map={"trades": "n/%s" % mon}, extra={"usd_raw": "usd/%s" % mon, "p": "p/%s" % mon, "usd_wo_best_day": "usd_wo_best_day/%s" % mon}),
          dict(ptr="bases/*", cell="база {0}", win=w, map={"trades": "n/%s" % mon}, extra={"usd_raw": "usd/%s" % mon})],
         same_data=1)

# ── П-12: Шарп клеток (free) — файлы; Шарп годовой в extra (ставка не названа)
P12X = dict(sr_year="sr_year", d_sr="d_sr", ci_lo="ci_lo", ci_hi="ci_hi", p_wy="p_wy", d_usd_raw="d_usd", passed="passed", refuted="refuted")
pick(142, "docs/archive/findings/p12-sharpe-free-2026-10-10-v171c-D.csv", [dict(where={}, cell="{pack} {cell}", win="янв–сен", extra=P12X)])
pick(142, "docs/archive/findings/p12-sharpe-free-2026-10-12-r2b.csv", [dict(where={}, cell="{pack} {cell}", win="янв–сен", extra=P12X)])
pick(142, "docs/archive/findings/p12-sharpe-free-2026-10-08.csv", [dict(where={"pack": "ext"}, cell="{pack} {cell}", win="янв–сен", extra=P12X)])

# ── П-18: финал Б — файл
pick(143, "docs/archive/findings/tk289-final-b-2026-10-21.json",
     [dict(ptr="B1_B", cell="B1 (финал Б)", map={"trades": "trades"}, extra={"sr_day_raw": "SR", "days": "days"}),
      dict(ptr="families/*", cell="семья {family}", extra={"p": "p", "p_holm_m17": "p_holm_m17", "pass_m17": "pass_m17", "tested": "tested"})],
     win="янв–сен")

# ── G-016 пампы (В-108: отчёты удалены, числа — из текста записи)
text(144, E, 64, [r("TK-419: ячеек различают «сигнал для поиска пампа»", extra={"cells_pass": 0, "cells": 36}),
                  r("TK-419: B в A, марк-аут 60 мин", extra={"markout_bps": -40.4}),
                  r("вар. 2 spot ob200", extra={"cells_pass": 0, "cells": 48}),
                  r("вар. 6 (θ 6/7 %), A: T≈P", extra={"cells": 12, "t_eq_p": 9, "confirmed": 0}),
                  r("события: A Bybit linear / B Gate", extra={"events_a": 5517, "instr_a": 40, "events_b": 3041, "instr_b": 21})], win="сентябрь 2026")
text(145, E, 65, [r("испытаний; A сент. и авг. «различает»", extra={"tests": 104, "pass_a": 0}),
                  r("B Gate авг.: «различает» из", extra={"pass": 0, "tests": 4})])

# ── Реестр E (сентябрь 2026): n — круги (сделки), «точка / нижняя» — bps на круг, P&L — колонка «P&L $1000»
text(146, E, 79, [r("48 форм (32 вырождены)", trades=45800000, extra={"bps_point": -7.6, "forms": 48, "degenerate": 32})], win="ZEC 13.09")
gap(147, "не запускалось", "смоук 4423 касания прошёл, боевого прогона нет (числа a/b владелец не назвал)", E, 80)
text(148, E, 81, [r("any · pct1-1to1-3600", trades=79, extra={"bps_point": 0.37, "bps_lower": -0.70}),
                  r("frontrun · pct0.5-1to1-7200", trades=79, extra={"bps_point": 0.26, "bps_lower": -0.45})], win="16–18.09")
text(149, E, 82, [r("pct1-half1to1-3600", trades=79, extra={"bps_point": 0.07, "bps_lower": -0.80}),
                  r("eat50x80", extra={"rounds_min": 225, "rounds_max": 238, "bps_point": -8})], win="16–18.09")
gap(150, "не запускалось", "пересечение «сила ≥ 100 % ∩ возраст ≥ 15 мин» = 3 касания/сутки — пусто", E, 83)
gap(152, "числа не вписаны", "в строке реестра только «все ≤ 0» — n и $ не вписаны", E, 85)
text(153, E, 86, [r("a45-bid", side="long", trades=89, extra={"bps_point": 6.65, "bps_lower": -1.16}),
                  r("a45-ask", side="short", trades=158, extra={"bps_point": -3.22, "bps_lower": -0.80}),
                  r("s100-bid", side="long", trades=52, extra={"bps_point": 0.97, "bps_lower": -0.39}),
                  r("s100-ask", side="short", trades=3, extra={"bps_point": -0.23}),
                  r("лонг, всего", side="long", win="16–18.09", extra={"pnl_usd_1000": 171, "win_pct": 46, "dd_usd": -39}),
                  r("шорт, всего", side="short", win="16–18.09", extra={"pnl_usd_1000": -239}),
                  r("лонг с 19.09 (флэт)", side="long", win="с 19.09", trades=116, extra={"pnl_usd": 162, "pnl_usd_19th": 16, "lower": -0.75}),
                  r("шорт с 19.09 (флэт)", side="short", win="с 19.09", trades=134, extra={"pnl_usd": -234, "pnl_usd_19th": -17})])
text(154, E, 87, [r("a15-s10-bid", side="long", trades=75, extra={"bps_point": 2.1, "bps_lower": -0.76}),
                  r("a30-bid", side="long", trades=118, extra={"bps_point": 2.3, "bps_lower": -0.87}),
                  r("a60-bid", side="long", trades=41, extra={"bps_point": 8.3, "bps_lower": -1.7})], win="16–18.09")
text(155, E, 88, [r("a45-bid-e75", side="long", extra={"bps_point": 6.9, "bps_lower": -1.05}),
                  r("a45-bid-e60", side="long", extra={"bps_point": 4.7, "bps_lower": -1.8}),
                  r("6 клеток: n по порядку строки реестра", extra={"n_list": "33 / 60 / 64 / 119 / 35 / 42"})], win="16–18.09")
text(156, E, 89, [r("p4h-neg", side="long", extra={"bps_point": 16.4, "bps_lower": -1.7}),
                  r("b4h-neg", side="long", extra={"bps_point": 17.4, "bps_lower": -1.8}),
                  r("p4h-q50", side="long", extra={"bps_point": -3.5}),
                  r("p4h-q75", side="long", extra={"bps_point": -6.0}),
                  r("b4h-q50", side="long", extra={"bps_point": -2.6}),
                  r("ask-p4h-q25", side="short", extra={"bps_point": 1.9, "bps_lower": -2.6}),
                  r("ask-r1h", side="short", extra={"bps_point": -2.1}),
                  r("лонг после просадки пула 4 ч", side="long", win="16–18.09", extra={"pnl_usd_1000": 148, "win_pct": 66, "dd_usd": -10}),
                  r("лонг после просадки BTC 4 ч", side="long", win="16–18.09", extra={"pnl_usd_1000": 187}),
                  r("H1 на 4 сутках (19-е флэт)", side="long", win="16–19.09", trades=60, extra={"pnl_usd_1000": 172, "win_pct": 58, "dd_usd": -16, "pnl_usd_19th": 14}),
                  r("9 наборов: n по порядку строки реестра", extra={"n_list": "32 / 46 / 25 / 15 / 31 / 17 / 69 / 36 / 6"})])
text(157, E, 90, [r("pct2-tk1-7200", side="long", trades=95, extra={"bps_point": 3.84, "bps_lower": -0.67}),
                  r("контроль 1to1 той же формы", side="long", trades=92, extra={"pnl_usd_1000": 155})], win="16–19.09")
text(158, E, 91, [r("pct2-1to1-14400 (4 ч)", side="long", trades=73, extra={"bps_point": 7.66, "bps_lower": -0.78, "pnl_usd_1000": 296}),
                  r("pct2-1to1-14400 с просадкой BTC", side="long", trades=38, extra={"bps_point": 11.8, "bps_lower": -0.67}),
                  r("удержание 60 с", side="long", extra={"pnl_usd_1000": -198}),
                  r("удержание 10 мин", side="long", extra={"pnl_usd_1000": -131}),
                  r("удержание 30 мин", side="long", extra={"pnl_usd_1000": 7}),
                  r("удержание 1 ч", side="long", extra={"pnl_usd_1000": 187}),
                  r("удержание 2 ч", side="long", extra={"pnl_usd_1000": 155})], win="16–19.09")
text(159, E, 92, [r("a90", side="long", trades=44, extra={"bps_point": 9.9, "bps_lower": -1.4}),
                  r("a120", side="long", trades=25, extra={"bps_point": 5.6, "bps_lower": -1.7}),
                  r("fr (только от фронтрана)", side="long", trades=116, extra={"bps_point": 5.4, "bps_lower": -0.84}),
                  r("u25", side="long", trades=61, extra={"bps_point": 9.0, "bps_lower": -1.2, "pnl_usd_1000": 131}),
                  r("u50", side="long", trades=20, extra={"bps_point": 6.1, "bps_lower": -3.2}),
                  r("a90, P&L", side="long", trades=37, extra={"pnl_usd_1000": 105, "pnl_usd_19th": -15})], win="16–19.09")
text(160, E, 93, [r("все 144 формы (88 форм с < 2 кругами)", side="long", trades=201, extra={"forms": 144, "forms_lt2": 88}),
                  r("ladder3x2..10-pct2-1to1-7200-ttl1800-gone50 (лучшая)", side="long", trades=2, extra={"bps_point": 4.71, "bps_lower": -0.10, "dsr": 0.009})], win="16–19.09")
gap(161, "предрегистрация", "E16 — заморозка формы для первых OOS-суток с 23.09; в реестре «ждём сутки», итога нет", E, 94)
text(162, E, 95, [r("96 форм (скальп-отскок)", extra={"forms": 96, "bps_min": -9, "bps_max": -5, "excess_min": -3, "excess_max": -1, "stick_pct_min": 55, "stick_pct_max": 96}),
                  r("a45-bid stack2-600: без прилипания → с ним", side="long", extra={"bps_without": -5.3, "bps_with": -7.1})], win="6 суток")
text(167, E, 100, [r("кандидат t-bid-btc1h-q1", side="long", trades=40, extra={"deposit_pct": -1.71}),
                   r("база t-bid-age-45", side="long", trades=63, extra={"deposit_pct": -3.53}),
                   r("сверх рынка (М19)", side="long", extra={"bps_per_trade": 128}),
                   r("позиция в худшую минуту", side="long", extra={"median_pct": -27.7, "worst_pct": -59.7})], win="e-crash 10–11.10.2025")
gap(168, "числа не вписаны", "E23: «пересчёт идёт — числа не вписаны»; прежние +$515 / +$272 недействительны (В-93)", E, 101)

# ── E24–E34: позиция 500 $ (--order-usd 500), $ «в сделках» = Σ по сделкам
text(169, E, 102, [r("кандидат BTC 1 ч", side="long", trades=826, usd=-124, extra={"trades_account": 809, "account_usd": -113, "account_pct": -4.5, "dd_pct": -6.4}),
                   r("BTC 4 ч, трейл 1/1", side="long", usd=102, extra={"account_usd": 98, "account_pct": 3.9}),
                   r("BTC 4 ч, тейк 1,75 % (счёт)", side="long", extra={"account_usd": 66, "account_pct": 2.6}),
                   r("без фильтра", side="long", usd=137, extra={"account_usd": 163, "account_pct": 6.5, "dd_pct": -15})], win="август 01–31.08", stake=500)
L170 = []
for st, n, vals in (("база", 2578, (137, -16, -58)), ("BTC 1 ч", 826, (-124, -170, -177)), ("BTC 4 ч", 523, (102, 59, 70))):
    for ex, v in zip(("трейл 1/1", "трейл 2/0,5", "тейк 1,75 %"), vals):
        L170.append(r("%s · стоп 2 %%, 4 ч, %s" % (st, ex), side="long", usd=v, **({"trades": n} if ex == "трейл 1/1" else {})))
text(170, E, 103, L170, win="август 01–31.08", stake=500)
L171 = []
for w, ns, us in (("сентябрь 01–23.09", (270, 213, 178, 168), (198, 87, 60, 85)), ("август 01–31.08", (826, 707, 555, 523), (-124, 17, 132, 102))):
    for h, n, u, acc in zip(("1 ч", "2 ч", "3 ч", "4 ч"), ns, us, (-113, 11, 130, 98)):
        L171.append(r("просадка BTC %s, трейл 1/1" % h, side="long", win=w, trades=n, usd=u, **({"extra": {"account_usd": acc}} if w.startswith("август") else {})))
text(171, E, 104, L171, stake=500)
L172 = []
for w, ns, us in (("август 01–31.08", (503, 238, 162), (106, -2, 27)), ("сентябрь 01–23.09", (152, 122, 97), (103, 118, 105))):
    for cap, n, u in zip(("без потолка", "потолок 5", "потолок 3"), ns, us):
        L172.append(r("BTC 4 ч, трейл 1/1, %s, без правила серии" % cap if (w.startswith("август") and cap == "без потолка") else "BTC 4 ч, трейл 1/1, %s" % cap,
                      side="long", win=w, trades=n, usd=u))
L172.append(r("BTC 4 ч, трейл 1/1, без потолка, с правилом серии 5 (блок BCH)", side="long", win="август 01–31.08", usd=113))
text(172, E, 105, L172, stake=500, same_data=1)


def deltas(rows_spec):
    out = []
    for cell, a, s in rows_spec:
        for w, v in (("август", a), ("сентябрь", s)):
            if v is None:
                continue
            ex = {"d_usd": v} if not isinstance(v, tuple) else {"d_usd_min": v[0], "d_usd_max": v[1]}
            out.append(r(cell, side="long", win=w, extra=ex))
    return out


text(173, E, 106, [r("база пересчёта", side="long", win="август", trades=503, usd=147, extra={"backtest_usd": 106, "reasons_match_pct": 97}),
                   r("база пересчёта", side="long", win="сентябрь", trades=152, usd=137, extra={"backtest_usd": 103, "reasons_match_pct": 96})]
     + deltas([("стоп 2,5 %", -20, 3), ("стоп 3 %", -17, 5), ("стоп под волатильность", (-26, -13), (-21, -12)), ("выход по BTC −0,5 %", -19, -31),
               ("выход по BTC −0,75 %", -2, -6), ("выход по BTC −1…−1,5 %", (-12, 2), (-11, -7))]), stake=500, same_data=1)
text(174, E, 107, deltas([("трейл по RV60 ×1 / 1,5 / 2", (21, 63), (-83, -36)), ("люстра 1,5–2,5σ4ч", (2, 22), (-101, -84)), ("разгон RV30 ≥ 2×", -13, -8),
                          ("разгон RV30 ≥ 3×", 17, 1), ("стоп по логарифму времени к 1 %", -25, 3), ("стоп по логарифму времени к 0,5 %", -45, -12)])
     + [r("разгон RV30 ≥ 3×: худший день (против базы)", side="long", win="август", extra={"worst_day_usd": -47, "base_worst_day_usd": -55}),
        r("разгон RV30 ≥ 3×: худший день (против базы)", side="long", win="сентябрь", extra={"worst_day_usd": -27, "base_worst_day_usd": -27}),
        r("стоп по логарифму времени к 1 %: худший день", side="long", win="август", extra={"worst_day_usd": -46}),
        r("стоп по логарифму времени к 1 %: худший день", side="long", win="сентябрь", extra={"worst_day_usd": -19})], stake=500, same_data=1)
text(175, E, 108, deltas([("откат min(1 %, 1·RV60)", 20, -13), ("откат min(1 %, 1,5·RV60)", -3, -5), ("откат min(1 %, 2·RV60)", 4, 0),
                          ("включение / откат по σ4ч", (-31, -14), (-13, 1)), ("люстра 2,5σ4ч для спокойных", 13, 6), ("разгон: только выход в минусе", 20, -1),
                          ("разгон: только поджатие в плюсе", -3, 2), ("разгон ×2,5", 16, 1), ("разгон ×3", 17, 1), ("разгон ×4", 4, 0),
                          ("разгон, окно 15 мин", 11, -15), ("разгон, окно 60 мин", 5, 0)]), stake=500, same_data=1)
text(176, E, 109, [r("база", side="long", win="август→сентябрь", usd=137), r("база", side="long", win="сентябрь→август", usd=147),
                   r("один общий вариант", side="long", win="август→сентябрь", usd=36, extra={"d_usd": -101}),
                   r("один общий вариант", side="long", win="сентябрь→август", usd=130, extra={"d_usd": -17}),
                   r("по семействам K = 2", side="long", win="август→сентябрь", usd=120, extra={"d_usd": -17}),
                   r("по семействам K = 2", side="long", win="сентябрь→август", usd=147, extra={"d_usd": 1}),
                   r("по семействам K = 3", side="long", win="август→сентябрь", usd=112, extra={"d_usd": -25}),
                   r("по семействам K = 3", side="long", win="сентябрь→август", usd=155, extra={"d_usd": 8})], stake=500)
text(177, E, 110, [r("устойчивость уровня монеты август→сентябрь, ρ: " + k, extra=v) for k, v in (
    ("RV по закрытиям (эталон)", {"rho": 0.63}), ("по размаху", {"rho_min": 0.62, "rho_max": 0.63}), ("без скачков", {"rho_min": 0.63, "rho_max": 0.66}),
    ("EWMA", {"rho": 0.78}), ("HAR", {"rho": 0.75}), ("собственная", {"rho": 0.71}), ("среднее рангов всех", {"rho": 0.81}))]
     + [r("прогноз хода 4 ч, ρ (все оценки)", extra={"rho_min": 0.73, "rho_max": 0.80}), r("связь с $, ρ (все оценки)", extra={"rho_min": -0.06, "rho_max": 0.10}),
        r("сделок главного варианта (август / сентябрь)", extra={"trades_aug": 503, "trades_sep": 150, "coins_aug": 75, "coins_sep": 49})])
text(178, E, 111, [r("база", side="long", win="август→сентябрь", usd=137), r("база", side="long", win="сентябрь→август", usd=147),
                   r("по семействам K = 2 (сводная волатильность)", side="long", win="август→сентябрь", usd=101, extra={"d_usd": -35}),
                   r("по семействам K = 2 (сводная волатильность)", side="long", win="сентябрь→август", usd=105, extra={"d_usd": -42}),
                   r("по семействам K = 3 (сводная волатильность)", side="long", win="август→сентябрь", usd=36, extra={"d_usd": -100}),
                   r("по семействам K = 3 (сводная волатильность)", side="long", win="сентябрь→август", usd=166, extra={"d_usd": 19})], stake=500)
text(179, E, 112, [r("ρ со стопом: скорость подхода 10 с", extra={"rho_aug": 0.13, "rho_sep": 0.10}),
                   r("ρ со стопом: σ 600 с", extra={"rho_aug": 0.18, "rho_sep": 0.10}),
                   r("ρ со стопом: удержание стены", extra={"rho_aug_min": 0.11, "rho_aug_max": 0.14, "rho_sep_min": -0.17, "rho_sep_max": -0.09}),
                   r("ρ со стопом: поглощение", extra={"rho_aug_min": 0.11, "rho_aug_max": 0.12, "rho_sep_min": -0.11, "rho_sep_max": -0.03}),
                   r("верхняя треть скорости подхода", side="long", win="август", usd=-36),
                   r("верхняя треть скорости подхода", side="long", win="сентябрь", usd=49),
                   r("сделок с касанием (август / сентябрь)", extra={"touch_aug": 440, "trades_aug": 503, "touch_sep": 129, "trades_sep": 152})], stake=500)
gap(180, "предрегистрация", "E35 — заморозка проверки на свежих сутках; первое чтение не раньше 100 сделок, итога в реестре нет", E, 113)

# ── Реестр M (замеры без сделок)
text(181, E, 119, [r("доля отскока: без порога → сила ×соседи ≥ 300 % → ≥ 1000 %", extra={"rebound_pct_0": 34, "rebound_pct_300": 58, "rebound_pct_1000": 64})])
text(182, E, 120, [r("markout после касания, $100k–1M", extra={"bps_per_h": -27}), r("markout после касания, ≥ $1M", extra={"bps_per_h": 42, "n": 34})])
XS = "docs/archive/findings/excursion-sigma-2026-09-18.md"
XQ = ("p50", "p75", "p90", "p95")
XA = ("le_0_25", "le_0_5", "le_1", "le_1_5", "le_2", "le_3")
XB = ("ge_0_25", "ge_0_5", "ge_1", "ge_1_5", "ge_2", "ge_3")
xs_rows = [r("σ_H по касаниям с силой ≥ 300 %, bps, H = " + h, line=ln, extra=dict(zip(("p25", "p50", "p75"), v)))
           for h, ln, v in (("60 с", 18, (14.8, 27.7, 51.7)), ("10 мин", 19, (52.6, 92.1, 157.6)), ("1 ч", 20, (128, 224, 369)), ("2 ч", 21, (182, 311, 499)))]
for title, keys, base, tab in (
        ("ход против отскока / σ_H (доля ≤ kσ, %)", XA, 34, (
            ("60 с", "отскок", 21814, (0.39, 0.95, 1.73, 2.35), (41, 57, 76, 87, 93, 97)), ("60 с", "пробой", 16136, (0.67, 1.33, 2.22, 3.02), (23, 40, 65, 79, 88, 95)),
            ("10 мин", "отскок", 21645, (0.54, 1.04, 1.67, 2.18), (29, 47, 74, 87, 94, 98)), ("10 мин", "пробой", 16041, (0.64, 1.17, 1.86, 2.40), (22, 41, 69, 84, 92, 97)),
            ("1 ч", "отскок", 21055, (0.56, 1.05, 1.71, 2.21), (26, 46, 73, 87, 93, 98)), ("1 ч", "пробой", 15621, (0.62, 1.11, 1.80, 2.37), (22, 42, 71, 86, 92, 97)),
            ("2 ч", "отскок", 20441, (0.55, 1.02, 1.61, 2.09), (25, 47, 74, 88, 94, 98)), ("2 ч", "пробой", 15151, (0.60, 1.08, 1.70, 2.23), (22, 43, 72, 87, 93, 98)))),
        ("ход за отскок / σ_H (доля ≥ kσ, %)", XB, 49, (
            ("60 с", "отскок", 21814, (0.60, 1.16, 1.96, 2.65), (74, 56, 30, 16, 10, 4)), ("60 с", "пробой", 16136, (0.37, 0.89, 1.62, 2.16), (57, 42, 22, 11, 6, 2)),
            ("10 мин", "отскок", 21645, (0.62, 1.13, 1.78, 2.35), (77, 58, 30, 15, 7, 3)), ("10 мин", "пробой", 16041, (0.54, 1.05, 1.66, 2.17), (71, 53, 27, 13, 6, 2)),
            ("1 ч", "отскок", 21055, (0.63, 1.11, 1.73, 2.23), (79, 59, 29, 14, 7, 2)), ("1 ч", "пробой", 15621, (0.59, 1.09, 1.76, 2.34), (75, 56, 28, 14, 7, 3)),
            ("2 ч", "отскок", 20441, (0.62, 1.08, 1.65, 2.21), (79, 59, 29, 12, 7, 2)), ("2 ч", "пробой", 15151, (0.59, 1.07, 1.73, 2.39), (76, 57, 27, 13, 7, 3))))):
    for i, (h, out, n, q, sh) in enumerate(tab):
        xs_rows.append(r("%s, H = %s, %s" % (title, h, out), line=base + i, extra=dict(n=n, **dict(zip(XQ, q)), **dict(zip(keys, sh)))))
xs_rows.append(r("касаний на входе (сила ×соседи ≥ 300 %, ≥ $1k), 94 монеты", line="10-11", replaces="строка 11", extra={"touches": 38150, "coins": 94}))
text(183, XS, None, xs_rows, win="09-16–09-17")
text(184, E, 122, [r("markout при силе ×поток ≥ 100 %", extra={"bps_per_h": 10.6}), r("возраст работает с, мин", extra={"age_min": 45})])
text(185, E, 123, [r("сила ≥ 100 % ∩ возраст ≥ 15 мин: касаний в сутки на пул", extra={"touches_per_day": 3})])
DL = "docs/archive/findings/density-lifetime-2026-09-19.md"
DB = "docs/archive/findings/density-bots-2026-09-19.md"
text(186, DL, None, [r("умерших сильных плотностей (полы В-66), 100 монет", line="5-10", replaces="строка 10", extra={"levels": 11224843, "coins": 100}),
                     *[r("жизнь плотности " + b, line=ln, extra={"levels": n, "share_pct": pc}) for b, ln, n, pc in (
                         ("< 10 с", 14, 7614747, 67.8), ("10–60 с", 15, 2388835, 21.3), ("1–10 мин", 16, 1139708, 10.2), ("10–60 мин", 17, 78875, 0.7), ("≥ 1 ч", 18, 2678, 0.02))],
                     r("квантили жизни, с (p90 / p99 — мин)", line=20, extra={"p10": 0.1, "p25": 0.5, "p50": 3.0, "p75": 16.7, "p90_min": 1.1, "p99_min": 8.1})], win="09-16–09-17")
text(186, DB, None, [r("чужие боты: выход при съедании плотности, % (их дефолты)", line=29, replaces="строка 65", extra={"trigger1_pct": 50, "close1_pct": 50, "trigger2_pct": 80}),
                     r("DensityBounceGuard: задержка до Binance (их пример, VPN), мс", line=46, replaces="строка 82", extra={"latency_ms": 178})])
text(187, E, 125, [r("монет < 100 касаний за двое суток", extra={"coins": 23}), r("CAP / AXL: касаний", extra={"touches": 0})])
text(188, E, 126, [r("задержка живыми ордерами, DOGE, 100 циклов, мс", extra={"place_ms": 4.2, "cancel_ms": 4.0, "taker_ms": 5.7, "cycles": 100})])
text(189, E, 127, [r("круги a45", side="long", extra={"bps_min": 12, "bps_max": 17}), r("круги a45", side="short", extra={"bps_min": -19, "bps_max": -16})])
text(190, E, 128, [r("усадка к касанию, a45, %", extra={"q25": 60, "q50": 75, "q75": 87}), r("усадка к касанию, s100, %", extra={"q25": 6, "q50": 20, "q75": 39})])
text(191, E, 129, [r("пул за сутки, %", extra={"d16": 1.9, "d17": 3.2, "d18": 6.9}), r("BTC за сутки, %", extra={"d16": 0.8, "d17": 0.2, "d18": 5.9}),
                   r("квартили pool_ret_4h, bps", extra={"q25": -1.1, "q50": 46, "q75": 95})], win="16–18.09")
text(192, E, 130, [r("клеток bid + ask = any", extra={"cells_bid_ask": 2784, "cells_any": 3360})])
text(193, E, 132, [r("116 сделок лонгов: очередь впереди / выметено / в стену (медиана, $)", side="long", trades=116,
                     extra={"queue_ahead_usd": 4860, "swept_usd": 1875, "into_wall_usd": 0, "swept_lt_queue_pct": 91})])
text(194, E, 133, [r("касаний по наборам", extra={"a45_bid": 386, "a45_bid_coins": 27, "a45_ask": 430, "a15_bid": 2029, "h1": 202}),
                   r("116 кругов pct2-1to1-3600: тейкер / без объёма / с объёмом", side="long", trades=116, extra={"taker": 89, "no_volume": 18, "with_volume": 9}),
                   r("спред в t0 1 тик, % касаний", extra={"pct": 63}), r("1@fr в t0: хоть что-то, %", extra={"pct": 5}),
                   r("лестница 3@20..70 через спред, %", extra={"pct_min": 87, "pct_max": 100}),
                   r("ноги до рынка, жизнь 300 с: хоть что-то / целиком, %", extra={"any_min": 37, "any_max": 51, "full": 11}),
                   r("ноги до рынка, жизнь 1800 с: хоть что-то / целиком, %", extra={"any_min": 48, "any_max": 61, "full_min": 15, "full_max": 21})], win="16–19.09")
text(195, E, 134, [r("подход D = 10, ≥ 45 мин", extra={"per_day": 266, "to_touch_pct": 20.8, "wait_med_s": 140}),
                   r("подход D = 20, ≥ 45 мин", extra={"per_day": 276, "to_touch_pct": 25.7, "wait_med_s": 196}),
                   r("подход D = 30, ≥ 45 мин", extra={"per_day": 214, "to_touch_pct": 21.3, "wait_med_s": 362})], win="16–19.09")
text(196, E, 135, [r("D = 20, t0, нога 0,05–0,1 %: хоть что-то / целиком, %", extra={"any": 6, "full": 2}),
                   r("D = 20, t0, нога 0,1–0,15 %: хоть что-то / целиком, %", extra={"any": 21, "full": 7, "net_1h_bps": 22.1, "n": 215}),
                   r("D = 20, t0, нога 0,15–0,2 %: хоть что-то / целиком, %", extra={"any": 24, "full": 1, "net_1h_bps": 26.1}),
                   r("D = 10, нога 0,05–0,1 %: хоть что-то, %; через спред, %", extra={"any": 29, "through_spread": 50})], win="16–19.09")
text(197, E, 136, [r("касаний; без продаж в стену, %", extra={"touches": 3047, "no_sells_pct": 77}),
                   r("нога исполнилась бы", extra={"filled": 104, "of": 684, "pct": 15.2})]
     + [r("markout 1 ч по доле съеденного B(300): " + k, extra={"bps": v}) for k, v in (("0", 23.9), ("0–10 %", 9.8), ("10–20 %", 3.7), ("20–50 %", 0.4), ("50+ %", -5.3))],
     win="16–19.09")
text(198, E, 138, [r("форма ladder3x2..20w2-pct2-1to1-7200-ttl1800, a45-bid", side="long", trades=128,
                     extra={"usd_full_lot": 377.45, "usd_filled_share": 298.17, "losers": 54, "winners": 74}),
                   r("фильтр pool_4h ≥ 68.77", side="long", trades=21, extra={"losers": 13, "cost_usd": -32.55}),
                   r("выход по снятию стены", side="long", trades=128, extra={"usd_delta": -188}),
                   r("стоп 1 %", side="long", trades=128, extra={"usd_delta": -74}),
                   r("соседняя форма …-3600-ttlwall-gone50", side="long", extra={"usd_full_lot": -15.77})], win="16–20.09")
text(199, E, 139, [r("стен моложе 45 мин: было → стало", extra={"before": 9, "after": 0}),
                   r("атомы-лидеры (разность в IQR)", extra={"mid_at_deadline_bps": -0.99, "favour_hold": -0.78, "adverse_hold": 0.61, "pool_4h": 0.35, "arm_dist_bps": -0.50})],
     win="16–20.09")
text(200, E, 140, [r("a45-bid (живая ветка)", side="long", extra={"net_bps": 29.5, "control_bps": 11.3, "excess_bps": 18.2, "excess_med_bps": 8.6, "days_pos": 4, "days": 5}),
                   r("a45-bid-b4h-neg (H1)", side="long", trades=66, extra={"net_bps": 42.3, "control_bps": 2.9, "excess_bps": 39.5, "excess_med_bps": 27.1}),
                   r("a15-bid", side="long", extra={"net_bps": 22.3, "control_bps": 13.8, "excess_bps": 8.5}),
                   r("a45-ask (шорты)", side="short", extra={"net_bps": -17.5, "control_bps": -22.0, "excess_bps": 4.5})], win="16–20.09")
text(201, E, 141, [r("судьи Jev: класс ошибки", extra={"right": 9, "of": 9}), r("судьи Jev: «гипотеза названа»", extra={"right": 8, "of": 8}),
                   r("судьи Jev: «числа со ссылками»", extra={"right": 7, "of": 8, "p_true_min": 0.48, "p_true_max": 0.85})])
text(202, E, 142, [r("архив ob200 Bybit за 09-21, МБ", extra={"btc_mb": 237, "hype_mb": 64, "wif_mb": 16, "atom_mb": 10}),
                   r("ATOM 09-15: сообщений", extra={"messages": 298000})])

# ── TK-493 стат-арбитраж (bps; ставка не названа)
text(203, E, 156, [r("B1 BingX crypto a0", extra={"bps_per_day_pair": 17.11}), r("B1 BingX crypto a1", extra={"bps_per_day_pair": 10.08, "plus1min_lower_t90": -1.80}),
                   r("C1: клеток с нижней > 0", extra={"cells_pass": 1, "cells": 345}), r("всего клеток A1/A2/B1/C1/D2", extra={"cells": 145, "candidates": 2})], win="15.10–04.11")
text(204, E, 157, [r("C2 схождения V1", trades=588, extra={"net_bps": -4.90, "y_d": -0.0323, "lo": -0.0502, "hi": -0.0144, "formation_bps": 12.36})], win="15.10–04.11")
text(205, E, 158, [r("C2 схождения V2", trades=613, extra={"net_bps": -5.01, "y_d": -0.0338, "lo": -0.0562, "hi": -0.0114, "formation_bps": 10.62})], win="15.10–04.11")
text(206, E, 159, [r("C2 с Gate, V1", trades=816, extra={"net_bps": -4.42}), r("C2 с Gate, V2", trades=853, extra={"net_bps": -3.45}),
                   r("пары с ногой Gate, V2", extra={"net_bps": -0.01})], win="15.10–04.11")
gap(207, "предрегистрация", "§13.24 записана ДО счёта (счёта нет; ждёт PASS критика и «go»); итог — отдельными записями TK-599", E, 160)
gap(208, "предрегистрация", "§13.24.5 записана ДО счёта (счёта нет); описательная таблица — после основного прогона", E, 161)

# ── П-42 §13.27 (TK-610): чтение августа — файл
for sd, side in (("L", "long"), ("S", "short")):
    pick(209, "docs/findings/p42-catboost-retitr-2026-11-19/read-aug.json",
         [dict(ptr="sides/%s" % sd, side=side, cell="A: C* без фильтра", map={"trades": "n_a", "usd": "sum_a", "mean_usd": "mean_a"}, extra={"per_day": "per_day_a"}),
          dict(ptr="sides/%s" % sd, side=side, cell="B: C* с фильтром CatBoost", map={"trades": "n_b", "usd": "sum_b", "mean_usd": "mean_b"},
               extra={"delta": "delta", "lower5": "lower5", "upper95": "upper95", "dsum": "dsum", "per_day": "per_day"})],
         win="август")

# ── П-42 TK-600: X против f12, этап 1 — файл
pick(223, "docs/findings/p42-toxic-flow-2026-11-18/stage1.csv",
     [dict(where={}, side="{side}", cell="{target} · рука X", map={"trades": "n_sel_X", "usd": "sum_sel_X", "mean_usd": "S0_X"}, extra={"D": "D", "p_D": "p_D", "SE_D": "SE_D", "blocks_pos": "blocks_pos"}),
      dict(where={}, side="{side}", cell="{target} · рука f12", map={"trades": "n_sel_f12", "usd": "sum_sel_f12", "mean_usd": "S0_f12"})],
     win="окно F U0 (60 сут.)")

Path(__file__).with_suffix(".json").write_text(json.dumps({"task": "TK-661", "items": items}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
print("items", len(items), "entries", len({i["entry"] for i in items}))
