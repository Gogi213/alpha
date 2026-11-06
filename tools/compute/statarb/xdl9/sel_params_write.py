#!/usr/bin/env python3
"""TK-493 S1 (G0, часть 2): подставляет в docs/research/P-37-params-2026-11-06.csv значения, ИЗМЕРЕННЫЕ на ФОРМИРОВАНИИ по П-37 r3.1 (каталоги, нуль A1 по базам,
клетки, entry_z, D2 по ряду d, корзины B1, σ_форм по активным парам-суткам) и обновляет строки решений F7. ОДИН коммит файла - ДО торгового счёта.
Формат строки: key,value,status,when,source (конец строки как в файле; нетронутые строки сохраняются байт в байт).
"""
import csv, hashlib, io, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

PARAMS = os.path.join(S.ROOT, "docs", "research", "P-37-params-2026-11-06.csv")
CELLS = os.path.join(S.ROOT, "docs", "research", "P-37-params-cells-2026-11-06.csv")
D = "docs/findings/xdl9-selection-2026-11-06/"
R2 = "docs/archive/xdl9-selection-r2-2026-11-06/"
MEAS = "измерено на формировании, подтвердить"
ASSIGN = "назначено без Судьи, подтвердить"
SRC = "из источника"
WHEN = "S1 06.11"
CRLF = chr(13) + chr(10)


def sha(p, n=12):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:n]


def main():
    with open(PARAMS, "rb") as fh:
        raw = fh.read().decode("utf-8")
    global CRLF
    CRLF = chr(13) + chr(10) if chr(13) + chr(10) in raw else chr(10)          # конец строки как в файле (в ветке сейчас LF)
    raw_lines = raw.split(CRLF)
    rows = S.read_csv(PARAMS)
    by = {r["key"]: r for r in rows}
    orig = {r["key"]: (raw_lines[i + 1], (r["value"], r["status"], r["when"], r["source"])) for i, r in enumerate(rows)}
    docs = S.DOCS
    cells = S.read_csv(CELLS)
    hl = S.read_csv(os.path.join(docs, "cells_hl.csv"))
    ez = S.read_csv(os.path.join(docs, "entry_z.csv"))
    a1 = S.read_csv(os.path.join(docs, "a1_catalogs.csv"))
    d2 = S.read_csv(os.path.join(docs, "d2_params.csv"))
    du = S.read_csv(os.path.join(docs, "d2_durations.csv"))
    sg = S.read_csv(os.path.join(docs, "sigma_formation.csv"))
    g9 = S.read_csv(os.path.join(docs, "g9_null_test.csv"))
    g9n = S.read_csv(os.path.join(docs, "g9_eg_vs_numpy.csv"))
    g9b = S.read_csv(os.path.join(docs, "g9b_b1_vs_numpy.csv"))
    cat = S.read_csv(os.path.join(docs, "catalogs.csv"))
    traded = [r for r in cells if r["status"].startswith("торгуется")]
    n_by = Counter(r["kind"] for r in traded)
    cs = sha(CELLS)
    src_cells = "docs/research/P-37-params-cells-2026-11-06.csv (sha256 %s)" % cs

    def setv(key, value, status=MEAS, when=WHEN, source=None):
        if key in by:
            r = by[key]
            r["value"], r["status"], r["when"] = value, status, when
            if source is not None:
                r["source"] = source
        else:
            r = dict(key=key, value=value, status=status, when=when, source=source or "")
            rows.append(r)
            by[key] = r

    c1 = [r for r in hl if r["kind"] == "C1" and r["W_min"]]
    def mean_c1(m, col):
        v = [float(r[col]) for r in c1 if r["m"] == m and r[col]]
        return sum(v) / len(v) if v else float("nan")
    ez_c1 = [r for r in ez if r["kind"] == "C1"]
    c1_traded = [r for r in ez_c1 if r["status"] == "торгуется"]
    c1_few = [r for r in ez_c1 if r["status"].startswith("не торгуется: мало сделок")]
    sel_c = [r for r in a1 if r["status"] == "отобрано"]
    not_done = [r for r in a1 if r["status"].startswith("отбор не выполнен")]
    tot_sel = sum(int(r["selected"] or 0) for r in a1)
    setv("halflife_median_min_by_cell", "%d клеток; %s; колонка hl_median_min (пары с phi вне (0,1) в медиану не входят)" % (len(cells), src_cells), source="P-37 §6.1; sel_cells.py")
    setv("window_min_by_cell", "%s; колонка window (W = max(2, round(m x медиана HL)), m = 4; у C1 m = 4|16|64 - три клетки; пол 2 - минимум ядра min_obs >= 2)" % src_cells,
         source="P-37 §5, §6.1; sel_cells.py")
    setv("entry_z_by_cell", "торгуется %d клеток (A1 %d, A2 %d, B1 %d, C1 %d; D2 %d без z); не торгуется: безубыточной точки нет на достижимой части сетки %d, мало сделок формирования %d, "
         "полураспад не измерен %d; %s; колонки entry_z, stop_z_a1; сетка 1.00-4.00 шаг 0.25, достижимо z <= sqrt(W-1), не меньше 20 сделок в пуле клетки"
         % (len(traded) - sum(1 for r in traded if r["kind"] == "D2"), n_by["A1"], n_by["A2"], n_by["B1"], n_by["C1"], n_by["D2"],
            sum(1 for r in ez if r["status"].startswith("не торгуется: безубыточной")), sum(1 for r in ez if r["status"].startswith("не торгуется: мало")),
            sum(1 for r in ez if r["status"].startswith("полураспад")), src_cells),
         source="P-37 §6.2; sel_entry.py; %sentry_z.csv, entry_grid.csv" % D)
    setv("max_hold_min_by_cell", "%s; колонка max_hold (z-правило: = W, у C1 для каждого из трёх окон; D2: 0)" % src_cells, source="P-37 §5, §6.1 [Н44]; sel_waves.py")
    setv("phi_outside_unit_share_by_cell", "%s; колонки phi_outside_unit / hl_pairs_ok / pairs" % src_cells, source="P-37 §6.1")
    setv("c1_unreachable_share_by_cell",
         "C1: клеток с полураспадом %d (по 3 окна); медиана HL базиса 0.61 мин; доля недостижимой части сетки (z > sqrt(W-1)) по окнам w4/w16/w64: %.3f/%.3f/%.3f; доля пар с "
         "недостижимым entry_z (W_пары < 17): %.3f/%.3f/%.3f; %scells_hl.csv колонки z_max, grid_unreachable_share, pairs_unreachable_share"
         % (len(c1) // 3, mean_c1("4", "grid_unreachable_share"), mean_c1("16", "grid_unreachable_share"), mean_c1("64", "grid_unreachable_share"),
            mean_c1("4", "pairs_unreachable_share"), mean_c1("16", "pairs_unreachable_share"), mean_c1("64", "pairs_unreachable_share"), D),
         source="P-37 §6.2 (а) [Н37]; sel_cells.py")
    setv("funding_k_by_cell", "D2 по ряду d = r_Y - r_X: %d клеток, k=1:%d k=3:%d k=6:%d; %sd2_params.csv (sha256 %s)"
         % (len(d2), sum(1 for r in d2 if r["k"] == "1"), sum(1 for r in d2 if r["k"] == "3"), sum(1 for r in d2 if r["k"] == "6"), D, sha(os.path.join(docs, "d2_params.csv"))),
         source="P-37 §6.3 [Н21, Н36]; sel_d2.py")
    setv("funding_hold_payments_by_cell", "D2: %sd2_params.csv колонка hold_payments (медианная длина эпизодов постоянного знака d в метках Y); распределение: %s"
         % (D, dict(sorted(Counter(r["hold_payments"] for r in d2).items()))), source="P-37 §6.3 [Н21, Н36]; sel_d2.py")
    ug = [float(r["unequal_grid_share"]) for r in d2 if r["unequal_grid_share"]]
    setv("d2_grid_unequal_share_by_cell", "доля пар с неравными сетками выплат по клеткам D2: среднее %.3f (min %.3f, max %.3f); %sd2_params.csv колонка unequal_grid_share"
         % (sum(ug) / len(ug), min(ug), max(ug), D), source="P-37 §5 п.(5); sel_d2.py")
    med_ok = sum(1 for r in du if r["median_vs_interval"].startswith("медиана"))
    med_bad = [r["cell"] for r in du if r["median_vs_interval"].startswith("МЕДИАНА")]
    no_tr = sum(1 for r in du if r["median_vs_interval"].startswith("нет закрытых"))
    setv("d2_position_durations_by_cell",
         "длительности позиций D2 на формировании (max_hold = 0): закрытых позиций %d, не закрыто к концу окна %d; клеток с медианой >= интервала выплаты Y: %d; медиана < интервала: %d (%s); "
         "нет закрытых сделок: %d; %sd2_durations.csv" % (sum(int(r["closed_positions"]) for r in du), sum(int(r["open_at_end_positions"]) for r in du), med_ok, len(med_bad),
                                                          "; ".join(med_bad), no_tr, D), source="P-37 §5 п.(4), аудит M-01; sel_d2.py")
    setv("bars_share_threshold_by_catalog",
         "%d каталогов (биржа x полоса), Kneedle по долям валидных минут относительно маски (США-полосы: минуты кассы, остальные: все минуты); %scatalogs.csv колонка threshold_share (sha256 %s)"
         % (len(cat), D, sha(os.path.join(docs, "catalogs.csv"))), source="P-37 §6.4; sel_catalog.py")
    setv("market_24_7_label_by_exchange_class", "по всем (биржа x класс): %s; %scatalogs.csv колонка label_247" % (dict(Counter(r["label_247"] for r in cat)), D),
         source="P-37 §6.7 [Н23]; прокси «оборот в выходные > 0»: есть валидные минуты (v > 0) в выходные формирования")
    setv("null_shift_days",
         "крипта: календарные сутки; остальные полосы: торговые дни (позиция pos(d) = число будних суток в [01.10, d), у субботы и воскресенья - позиция следующего понедельника; источник T[pos(d) - k]); "
         "сдвиги k = +1, +2, -1; общее пересечение 03.10-13.10 (сутки 2467-2477): 11 суток, из них будних 7",
         status=ASSIGN, source="P-37 §7.2 [Н38], отступление F7 (торговый день вне крипты) сохранено; sel_shift.py, тест test_statarb_sel_fdr.py; k = +1 совпадает с правилом F7")
    setv("null_shifts_r3", "{+1,+2,-1} суток для крипты (календарные), для остальных полос - торговые дни (null_shift_days)", status=ASSIGN, source="P-37 §7.2 [Н38]")
    setv("null_common_span_days_r3", "11 (03.10-13.10, сутки 2467-2477) для обеих единиц сдвига; требование CEO >= 10 суток выполнено; будних суток 7 (выборка США-полос: 7 касс x 390 мин)",
         status="производное", source="P-37 §7.2: формирование 14 суток, сдвиги +1, +2, -1; sel_shift.check_span")
    setv("null_partitions", "3 разбиения для порога t* (x 3 сдвига = 9 нулевых наборов на каталог) + 1 контрольное (x 3 сдвига) для нулевого теста G9", status=ASSIGN,
         source="P-37 §7 [Н6]; контрольное разбиение добавлено в F7")
    setv("null_units_bases",
         "k0(t), R(t), n0 и M - в единицах различных баз; жадное паросочетание по возрастанию статистики (при равенстве по паре индексов символов), набор = реальные пары каталога ИЛИ нулевые пары "
         "одного разбиения при одном сдвиге (9 наборов, паросочетание каждого отдельно, k0 и n0 суммируются); R - принятые реальные пары <= t; торгуются принятые паросочетанием реальные пары "
         "<= t* (каждая база не более одного раза); k0(t*) >= 5 в этих единицах",
         status=ASSIGN, source="P-37 §7.3а [Н48]; sel_fdrlib.py (зафиксировано в коде коммитом 328d3ba7 ДО пересчёта); оптимистичность оценки в синтетике: тест test_statarb_sel_fdr.py")
    setv("null_matching_rule", "жадное паросочетание (не максимальное): префиксное свойство - принятые пары <= t равны жадному результату на подграфе пар <= t; размер полного паросочетания <= s/2",
         status=ASSIGN, source="P-37 §7.3а (iv); sel_fdrlib.greedy_matching")
    setv("t_star_by_catalog", "%d каталогов с отбором из %d с отчётом (отбор не выполнен: %d, нет отбора: остальные); отобрано пар %d; %sa1_catalogs.csv колонка t_star (sha256 %s); таблица t* +- оценка FDR: %sa1_tstar_table.csv"
         % (len(sel_c), len(a1), len(not_done), tot_sel, D, sha(os.path.join(docs, "a1_catalogs.csv")), D), source="P-37 §7; sel_fdr.py")
    setv("null_pairs_n0_by_catalog", "%sa1_catalogs.csv колонки n0_base (в единицах баз: сумма размеров паросочетаний 9 наборов) и n0_pairs (A x B, сумма по 9 наборам)" % D, source="P-37 §7")
    setv("real_pairs_M_by_catalog", "%sa1_catalogs.csv колонки M_base (размер паросочетания реальных пар) и M_pairs (различные пары «внутри половины» хотя бы в одном из разбиений 1-3)" % D, source="P-37 §7")
    setv("coverage_share_by_catalog", "%sa1_catalogs.csv колонка coverage = M_pairs / все пары каталога (ожидаемо 0.875 при 3 разбиениях)" % D, source="P-37 §7 п.2")
    b1 = os.path.join(docs, "b1_baskets.csv")
    setv("baskets_b1", "корзин %d (цели с тремя компонентами по корреляции доходностей формирования), исключено целей %d; %sb1_excluded.csv; %sb1_baskets.csv, sha256 %s"
         % (len(S.read_csv(b1)), len(S.read_csv(os.path.join(docs, "b1_excluded.csv"))), D, D, sha(b1)),
         source="план F7b; sel_b1.py; G9b %sg9b_b1_vs_numpy.csv" % D)
    cnt = Counter(r["kind"] for r in hl)
    setv("cells_total", "клеток без рук: %s (C1 - три окна); торгуется z-правило %d (клеток-рук %d), D2 %d; не торгуется %d (%s); всего клеток-рук в волнах %d"
         % (dict(cnt), len(traded) - n_by["D2"], 2 * (len(traded) - n_by["D2"]), n_by["D2"], len(cells) - len(traded),
            dict(Counter(r["kind"] for r in cells if not r["status"].startswith("торгуется"))), 2 * (len(traded) - n_by["D2"]) + n_by["D2"]),
         source="sel_waves.py; %scells_not_traded.csv" % D)
    sm = [r for r in sg if r["sigma_bps_per_day_per_active_pair"] != "не измерена"]
    setv("sigma_formation_by_cell", "%s колонка sigma_formation_bps_day_per_active_pair: измерено %d клеток-рук из %d, не измерена (меньше 5 суточных значений или нет закрытых сделок 08-14.10) %d; "
         "нормировка - активные пары-сутки (--job-day-out); %ssigma_formation.csv (sha256 %s)" % (src_cells, len(sm), len(sg), len(sg) - len(sm), D, sha(os.path.join(docs, "sigma_formation.csv"))),
         source="P-37 §6.6 [Н13], аудит M-07/P-02; sel_sigma.py")
    setv("sel_form_window_min_obs",
         "EG-окно 03.10-13.10 (общее пересечение сдвигов, 11 суток, 15840 мин, сутки 2467-2477); form --from-day 2478 --window-days 11 --block-chunks 6 (блок 768 мин, 21 блок, половины 7680 | 8160) "
         "--min-obs 300 (= n_ret_min) --alpha 1e9 (не порог: статистики всех пар)", status=ASSIGN, source="P-37 §7.2; sel_form_all.sh; бинарь form tk486@60e61cea не менялся")
    setv("sel_pair_statistic", "t = max(t_first, t_second) в лучшем направлении form (эквивалентно p = max(p1, p2) из select.rs без усечения p)", status=ASSIGN, source="P-37 §7 п.3; sel_fdr.py")
    setv("sel_catalog_min_symbols", "4 (меньше - нулевых пар нет: каталог без отбора); M или n0 меньше 10 или 9 (в единицах баз) - «нет отбора: мало пар»", status=ASSIGN, source="P-37 §7 п.3; sel_catalog.py, sel_fdrlib.py")
    setv("sel_entry_cost_rule",
         "entry_z* = наименьшее ДОСТИЖИМОЕ z сетки (z <= sqrt(W-1)), при котором (1) сделок в пуле клетки на формировании >= 20 и (2) средний валовый результат сделки >= 2 x средняя комиссия сделки клетки "
         "(12.6 bps при b = 1); нет z с (2) - «безубыточной точки нет на достижимой части сетки»; (2) только при < 20 сделок - «мало сделок формирования»; число не подставляется; 90 %-интервал среднего "
         "валового - сутки как кластеры, t с df = суток со сделками - 1", status=ASSIGN, source="P-37 §6.2 (а)-(в) [Н37, Н39]; sel_entry.py")
    setv("sel_entry_run", "ядро xdl9-4ef45cc8 с --rearm (C1 ещё --delay-x 1), панели ~/xdl9/form (обрезаны по 15.10 00:00), задания 01.10-14.10, exit_z 0, stop_z inf, max_hold = W, без фандинга; только достижимые z",
         status=ASSIGN, source="P-37 §6.2, §5 [Н35]")
    setv("sel_c1_result",
         "C1: %d клеток (115 пар бирж x класс, по 3 окна); торгуется %d (%s); не торгуется: мало сделок формирования %d, безубыточной точки нет на достижимой части сетки %d, полураспад не измерен %d; "
         "наибольший средний валовый на достижимом z: w4 %.1f, w16 %.1f, w64 %.1f bps против 25.2 bps (2 x круг 12.6)"
         % (len(ez_c1), len(c1_traded), "; ".join("%s z=%s N=%s валовый %s [%s; %s]" % (r["cell"], r["entry_z"], r["trades_at_z"], r["mean_gross_bps"], r["gross_lo90"], r["gross_hi90"]) for r in c1_traded),
            len(c1_few), sum(1 for r in ez_c1 if r["status"].startswith("не торгуется: безубыточной")), sum(1 for r in ez_c1 if r["status"].startswith("полураспад")),
            *[max(float(g["mean_gross_bps"]) for g in S.read_csv(os.path.join(docs, "entry_grid.csv")) if g["cell"].startswith("C1|") and g["cell"].endswith("|w%s" % m) and g["mean_gross_bps"])
              for m in (4, 16, 64)]),
         status=MEAS, source="%sentry_z.csv, entry_grid.csv; пола W сверх протокола нет (прежняя проба W = 60, 240 F7 снята)" % D)
    setv("sel_d2_max_hold", "0 (выключен): позиция D2 закрывается по правилу или остаётся открытой к концу окна (open_at_end)", status=ASSIGN, source="P-37 r3 §5 [Н36]")
    setv("sel_d2_window_min_obs", "60 / 60 / refresh 60 (ядром при правиле funding не используются; прогрев)", status=ASSIGN, source="sel_waves.py")
    setv("sel_d2_funding_g7",
         "G7 пройден в F6 (statarb-xdl9-gates-2026-11-06.md); фандинг формирования - только строки time_ms < 15.10 00:00 (~/xdl9/funding_form, sel_deck_prep.py fundcut: разбирается только метка времени); "
         "ряд d = r_Y - r_X на метках Y - d2_diff_funding.py (масса сходится на каждой паре)", status=MEAS, source="docs/findings/statarb-xdl9-gates-2026-11-06.md; sel_d2.py")
    setv("funding_form_cut", "~/xdl9/funding_form/<биржа>/<SYM>.csv.gz: фандинг только формирования (time_ms < 1 792 022 400 000 = 15.10 00:00 UTC); строк 455 981 в 5560 файлах; остановка на первой строке >= границы по первому полю",
         status=ASSIGN, source="sel_deck_prep.py fundcut; торговое окно в S1 не читалось")
    setv("sel_b1_rule",
         "цель + 3 компоненты с наибольшей корреляцией доходностей формирования внутри каталога (биржа x полоса), n_ret >= 300 с каждой, общих минут с доходностью у четырёх ног >= 300; без дублей по базе "
         "(основные контракты - один на актив); безусловна при зелёном G9b (Н25)", status=ASSIGN, source="P-37 §5 / план F7b; sel_b1.py")
    setv("sel_wave_window", "from_day 2479 (15.10) to_day 2500 (05.11 не включая); min-span 1440; флаги: --rearm (кроме D2), --trades-out --trades-ext --trade-jobs всех, --open-out --mtm-open, --cells-out, --ext-out, --job-day-out, --daily-out",
         status=ASSIGN, source="sel_waves.py; P-37 §14, §18")
    setv("sel_wave_binary", "~/xdl9/bin/statarb-xdl9-4ef45cc8 (tk493-xdl9, без PGO); sha256 969db927...; бинарь form - statarb-tk486-60e61cea", status=ASSIGN, source="count_binary; docs/findings/xdl9-kernel-fixes-2026-11-06.md")
    n_hold_b = sum(int(r["k0_hold_base"]) for r in g9 if r["status"] == "отобрано")
    e_hold_b = sum(float(r["k0_hold_exp_base"]) for r in g9 if r["status"] == "отобрано")
    n_hold_p = sum(int(r["k0_hold_pairs"]) for r in g9 if r["status"] == "отобрано")
    e_hold_p = sum(float(r["k0_hold_exp_pairs"]) for r in g9 if r["status"] == "отобрано")
    verdict = "пройден" if n_hold_b <= e_hold_b and n_hold_p <= e_hold_p else "пройден условно"
    setv("sel_gate_g9",
         "G9 (%s): EG form против numpy на %d парах - %d расхождений (макс. относительное |dt| %s, |dbeta| %s); нулевой тест на контрольном разбиении 4 x 3 сдвига, каталоги с отбором: прошло t* %d "
         "против ожидаемых %.1f в единицах баз, %d против %.1f в единицах пар"
         % (verdict, len(g9n), sum(1 for r in g9n if r["status"] != "ok"), max(float(r["rel_dt_max"]) for r in g9n if r["rel_dt_max"]), max(float(r["rel_dbeta"]) for r in g9n if r["rel_dbeta"]),
            n_hold_b, e_hold_b, n_hold_p, e_hold_p), status=MEAS, source=D + "g9_eg_vs_numpy.csv, g9_null_test.csv")
    setv("g9_status_result", "%s: прошло %d <= ожидалось %.1f (единицы баз) и %d <= %.1f (единицы пар) по сумме каталогов с отбором" % (verdict, n_hold_b, e_hold_b, n_hold_p, e_hold_p),
         status=MEAS, source="правило g9_status_rule; %sg9_null_test.csv" % D)
    setv("sel_gate_g9b", "корзины B1: %d целей, %d расхождений (макс. |d corr| %s)" % (len(g9b), sum(1 for r in g9b if r["status"] != "ok"), max(r["max_dcorr"] for r in g9b)),
         status=MEAS, source=D + "g9b_b1_vs_numpy.csv")
    setv("sel_wave_controls", "волна 4 (wave4.sh): контроль §11.8 «все пары каталога без отбора» для торгуемых клеток A1 (%d клеток); задания пишет sel_allpairs_jobs.py на Deck; контроли §11.1-11.4 и срез «без 17» §11.9 - не в S1"
         % n_by["A1"], status=ASSIGN, source="P-37 §11.8, §18 п.4, п.8")
    import json
    meta = json.load(open(os.path.join(S.SEL, "catalog_meta.json"), encoding="utf-8"))
    setv("sel_us_nonus_rule_counts", "активов классов акции/ETF/индексы %d, не-США %d (по таблице asset_map после D-01); контрактов теста %d; %ssession_assets.csv" % (meta["assets"], meta["nonus_assets"], meta["n_contracts"], D),
         status=MEAS, source="sel_catalog.py; catalog_meta.json")
    setv("params_state", "S1 06.11: значения отбора пересчитаны по П-37 r3.1 (окно EG 11 суток, нуль {+1,+2,-1} в единицах баз, C1 три окна, D2 по ряду d, entry_z на 4ef45cc8 --rearm, σ по активным парам-суткам); "
         "коммит ДО торгового счёта (G0 часть 2); F7 (58af894f) - r2, в docs/archive/xdl9-selection-r2-2026-11-06/", status=SRC, when=WHEN, source="")
    # ссылки на r2-таблицы в нетронутых строках - на архив
    for r in rows:
        if "docs/findings/xdl9-selection-2026-11-06/" in r["value"] + r["source"] and r["when"] != WHEN:
            r["value"] = r["value"].replace("docs/findings/xdl9-selection-2026-11-06/", R2)
            r["source"] = r["source"].replace("docs/findings/xdl9-selection-2026-11-06/", R2)
    with open(PARAMS, "w", encoding="utf-8", newline="") as fh:
        fh.write(raw_lines[0] + CRLF)
        for r in rows:
            cur = (r["value"], r["status"], r["when"], r["source"])
            if r["key"] in orig and orig[r["key"]][1] == cur:
                fh.write(orig[r["key"]][0] + CRLF)
            else:
                buf = io.StringIO()
                csv.writer(buf, lineterminator="").writerow([r["key"], *cur])
                fh.write(buf.getvalue() + CRLF)
    print("строк", len(rows), dict(Counter(r["status"] for r in rows)), "G9", verdict)


if __name__ == "__main__":
    main()
