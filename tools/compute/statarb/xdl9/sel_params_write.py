#!/usr/bin/env python3
"""TK-493 S1/S2 (G0, часть 3): подставляет в docs/research/P-37-params-2026-11-06.csv значения, ИЗМЕРЕННЫЕ на ФОРМИРОВАНИИ по П-37 r3.2 (каталоги и правило США/не-США по меткам биржи,
нуль A1 на рёбрах реального графа в единицах баз, клетки с HL(лаг 1) и HL(лаг 15), entry_z по варианту «закрытые + MTM», D2 по ряду d с ориентацией по интервалу выплат,
корзины B1, σ_форм) и обновляет строки решений. ОДИН коммит файла - ДО торгового счёта.
Формат строки: key,value,status,when,source (конец строки как в файле; нетронутые строки сохраняются байт в байт).
Хеши таблиц (A-27, аудит №2) считаются по СОДЕРЖИМОМУ git-блоба: CRLF рабочей копии приводится к LF (в индексе .csv лежит с LF), поэтому хеш воспроизводится из git.
"""
import csv, hashlib, io, json, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

PARAMS = os.path.join(S.ROOT, "docs", "research", "P-37-params-2026-11-06.csv")
CELLS = os.path.join(S.ROOT, "docs", "research", "P-37-params-cells-2026-11-06.csv")
D = "docs/findings/xdl9-selection-2026-11-06/"
S1A = "docs/archive/xdl9-selection-s1-2026-11-06/"
R2 = "docs/archive/xdl9-selection-r2-2026-11-06/"
MEAS = "измерено на формировании, подтвердить"
ASSIGN = "назначено без Судьи, подтвердить"
SRC = "из источника"
WHEN = "S2 07.11"
CRLF = chr(13) + chr(10)
BIN_PGO_SHA = "65e02a49a0f09f2bee57b87e20eac9b2a816248ef99a98b8bf496ae8cea83097"
BIN_PLAIN_SHA = "d873aea99771d615343ac6478a6e6542bbbb12cc8bdffd1de4b5c712f7116b3b"


def sha(p, n=12):
    """sha256 содержимого с приведением CRLF к LF (= git-блоб .csv): воспроизводится из git (A-27)."""
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()[:n]


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def main():
    with open(PARAMS, "rb") as fh:
        raw = fh.read().decode("utf-8")
    global CRLF
    CRLF = chr(13) + chr(10) if chr(13) + chr(10) in raw else chr(10)          # конец строки как в файле
    raw_lines = raw.split(CRLF)
    rows = S.read_csv(PARAMS)
    by = {r["key"]: r for r in rows}
    orig = {r["key"]: (raw_lines[i + 1], (r["value"], r["status"], r["when"], r["source"])) for i, r in enumerate(rows)}
    docs = S.DOCS
    cells = S.read_csv(CELLS)
    hl = S.read_csv(os.path.join(docs, "cells_hl.csv"))
    ez = S.read_csv(os.path.join(docs, "entry_z.csv"))
    eg = S.read_csv(os.path.join(docs, "entry_grid.csv"))
    a1 = S.read_csv(os.path.join(docs, "a1_catalogs.csv"))
    d2 = S.read_csv(os.path.join(docs, "d2_params.csv"))
    d2p = S.read_csv(os.path.join(docs, "d2_pairs.csv"))
    du = S.read_csv(os.path.join(docs, "d2_durations.csv"))
    sg = S.read_csv(os.path.join(docs, "sigma_formation.csv"))
    g9 = S.read_csv(os.path.join(docs, "g9_null_test.csv"))
    g9n = S.read_csv(os.path.join(docs, "g9_eg_vs_numpy.csv"))
    g9b = S.read_csv(os.path.join(docs, "g9b_b1_vs_numpy.csv"))
    cat = S.read_csv(os.path.join(docs, "catalogs.csv"))
    au = S.read_csv(os.path.join(docs, "us_rule_audit40.csv"))
    af = S.read_csv(os.path.join(docs, "us_rule_nonus_flagged.csv"))
    traded = [r for r in cells if r["status"].startswith("торгуется")]
    n_by = Counter(r["kind"] for r in traded)
    z_traded = [r for r in traded if r["kind"] != "D2"]
    n_z_total = sum(1 for r in hl if r["kind"] != "D2")
    n_copies = sum(int(r["a1_equals_a0"] or 0) for r in z_traded)
    n_hands = 2 * len(z_traded) + n_by["D2"]
    cs = sha(CELLS)
    src_cells = "docs/research/P-37-params-cells-2026-11-06.csv (sha256 %s по git-блобу)" % cs
    meta = json.load(open(os.path.join(S.SEL, "catalog_meta.json"), encoding="utf-8"))

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

    # ---------------------------------------------------------------- клетки: полураспад, окно, entry_z
    c1 = [r for r in hl if r["kind"] == "C1" and r["W_min"]]

    def mean_c1(m, col):
        v = [fnum(r[col]) for r in c1 if r["m"] == m and r[col]]
        return sum(v) / len(v) if v else float("nan")

    ez_c1 = [r for r in ez if r["kind"] == "C1"]
    c1_traded = [r for r in ez_c1 if r["status"] == "торгуется"]
    c1_few = [r for r in ez_c1 if r["status"].startswith("не торгуется: мало сделок")]
    hl1_c1 = sorted(fnum(r["hl_median_min"]) for r in c1 if r["m"] == "4" and r["hl_median_min"])
    ratio = {}
    for k in ("A1", "A2", "B1", "C1"):
        v = sorted(fnum(r["hl15_over_hl1"]) for r in hl if r["kind"] == k and r["hl15_over_hl1"] and (k != "C1" or r["m"] == "4"))
        ratio[k] = (v[len(v) // 2], v[0], v[-1], len(v)) if v else (float("nan"),) * 3 + (0,)
    setv("halflife_median_min_by_cell", "%d клеток; %s; колонка hl_median_min (HL по лагу 1; пары с phi вне (0,1) в медиану не входят) и hl15_median_min (HL по лагу 15, диагностика D14)"
         % (len(cells), src_cells), source="P-37 §6.1; sel_cells.py")
    setv("window_min_by_cell", "%s; колонка window (W = max(2, round(m x медиана HL лага 1)), m = 4; у C1 m = 4|16|64 - три клетки; пол 2 - минимум ядра min_obs >= 2)" % src_cells,
         source="P-37 §5, §6.1; sel_cells.py")
    setv("entry_z_by_cell",
         "торгуется %d z-клеток из %d (A1 %d, A2 %d, B1 %d, C1 %d; D2 %d без z); не торгуется: безубыточной точки нет на достижимой части сетки %d, мало сделок формирования %d, "
         "полураспад не измерен %d; ворота по варианту «закрытые + MTM» (r3.2, D10: позиции, открытые на конце формирования, входят оценкой по последнему валидному close; --mtm-open); %s; "
         "колонки entry_z, stop_z_a1, closed_at_entry_z_formation, mtm_at_entry_z_formation, a1_equals_a0; сетка 1.00-4.00 шаг 0.25, достижимо z <= sqrt(W-1), не меньше 20 позиций в пуле клетки"
         % (len(z_traded), n_z_total, n_by["A1"], n_by["A2"], n_by["B1"], n_by["C1"], n_by["D2"],
            sum(1 for r in ez if r["status"].startswith("не торгуется: безубыточной")), sum(1 for r in ez if r["status"].startswith("не торгуется: мало")),
            sum(1 for r in ez if r["status"].startswith("полураспад")), src_cells),
         source="P-37 §6.2 [Н60]; sel_entry.py; %sentry_z.csv, entry_grid.csv" % D)
    setv("max_hold_min_by_cell", "%s; колонка max_hold (z-правило: = W, у C1 для каждого из трёх окон; D2: 0)" % src_cells, source="P-37 §5, §6.1 [Н44]; sel_waves.py")
    setv("phi_outside_unit_share_by_cell", "%s; колонки phi_outside_unit / hl_pairs_ok / pairs" % src_cells, source="P-37 §6.1")
    setv("c1_unreachable_share_by_cell",
         "C1: клеток с полураспадом %d (по 3 окна); медиана HL базиса %.2f мин (медиана по клеткам, лаг 1); доля недостижимой части сетки (z > sqrt(W-1)) по окнам w4/w16/w64: %.3f/%.3f/%.3f; доля пар с "
         "недостижимым entry_z (W_пары < 17): %.3f/%.3f/%.3f; %scells_hl.csv колонки z_max, grid_unreachable_share, pairs_unreachable_share"
         % (len(c1) // 3, hl1_c1[len(hl1_c1) // 2] if hl1_c1 else float("nan"), mean_c1("4", "grid_unreachable_share"), mean_c1("16", "grid_unreachable_share"),
            mean_c1("64", "grid_unreachable_share"), mean_c1("4", "pairs_unreachable_share"), mean_c1("16", "pairs_unreachable_share"), mean_c1("64", "pairs_unreachable_share"), D),
         source="P-37 §6.2 (а) [Н37]; sel_cells.py")
    setv("hl_lag15_print",
         "в params и отчёте рядом печатаются HL(лаг 1) и HL(лаг 15) (cells_hl.csv: hl_median_min, hl15_median_min, hl15_over_hl1); сетка окон C1 W = m x HL(лаг 1), m = 4|16|64, и W остальных видов "
         "не меняются; HL15 / HL1 (медиана по клеткам [мин; макс], клеток): A1 %.2f [%.2f; %.2f] (%d), A2 %.2f [%.2f; %.2f] (%d), B1 %.2f [%.2f; %.2f] (%d), C1 (m = 4) %.2f [%.2f; %.2f] (%d)"
         % (ratio["A1"][0], ratio["A1"][1], ratio["A1"][2], ratio["A1"][3], ratio["A2"][0], ratio["A2"][1], ratio["A2"][2], ratio["A2"][3],
            ratio["B1"][0], ratio["B1"][1], ratio["B1"][2], ratio["B1"][3], ratio["C1"][0], ratio["C1"][1], ratio["C1"][2], ratio["C1"][3]),
         status=ASSIGN, source="P-37 §6.1, §15.1 [Н64]; D14; L5-05; sel_cells.py (hl15_of)")

    # ---------------------------------------------------------------- D2
    setv("funding_k_by_cell", "D2 по ряду d = r_Y - r_X (Y - нога с большим интервалом выплат): %d клеток, k=1:%d k=3:%d k=6:%d; %sd2_params.csv (sha256 %s)"
         % (len(d2), sum(1 for r in d2 if r["k"] == "1"), sum(1 for r in d2 if r["k"] == "3"), sum(1 for r in d2 if r["k"] == "6"), D, sha(os.path.join(docs, "d2_params.csv"))),
         source="P-37 §6.3 [Н21, Н36]; sel_d2.py")
    setv("funding_hold_payments_by_cell", "D2: %sd2_params.csv колонка hold_payments (медианная длина эпизодов постоянного знака d в метках Y); распределение: %s"
         % (D, dict(sorted(Counter(r["hold_payments"] for r in d2).items()))), source="P-37 §6.3 [Н21, Н36]; sel_d2.py")
    ug = [float(r["unequal_grid_share"]) for r in d2 if r["unequal_grid_share"]]
    gk = Counter(r["grid_kind"] for r in d2p)
    ob = Counter(r["orient_basis"] for r in d2p)
    setv("d2_grid_unequal_share_by_cell",
         "доля пар с неравными сетками выплат по клеткам D2 (после ориентации): среднее %.3f (min %.3f, max %.3f); grid_kind пар: равные %d, Y реже %d, сдвиг %d (из %d); %sd2_params.csv "
         "колонки unequal_grid_share, share_grid_equal, share_y_rarer, share_shift; пары - %sd2_pairs.csv"
         % (sum(ug) / len(ug), min(ug), max(ug), gk["равные"], gk["Y реже"], gk["сдвиг"], len(d2p), D, D), source="P-37 §5 п.(5), п.(6); sel_d2.py")
    setv("d2_orientation_rule",
         "D2 (только): Y := нога с БОЛЬШИМ медианным интервалом выплат на формировании (ничья или нет данных - по обороту, как для C1); колонка grid_kind (равные | Y реже | сдвиг) и доля сделок D2 по ней - в "
         "отчёте. Реализовано: sel_cells.orient_d2 по funding_interval_form.csv (funding_intervals.py: медиана расстояний между выплатами, ч); пар D2 %d: по интервалу без смены %d, со сменой Y/X %d, "
         "по обороту (ничья / нет данных) %d; grid_kind: равные %d, Y реже %d, сдвиг %d"
         % (len(d2p), ob["интервал"], ob["интервал (смена)"], ob["оборот"], gk["равные"], gk["Y реже"], gk["сдвиг"]),
         status=ASSIGN, source="P-37 §5, §6.3 [Н55]; D5 i; L3-01")
    med_ok = sum(1 for r in du if r["median_vs_interval"].startswith("медиана"))
    med_bad = [r["cell"] for r in du if r["median_vs_interval"].startswith("МЕДИАНА")]
    no_tr = sum(1 for r in du if r["median_vs_interval"].startswith("нет закрытых"))
    setv("d2_position_durations_by_cell",
         "длительности позиций D2 на формировании (max_hold = 0; ядро с --no-entry-before-from --mtm-open): закрытых позиций %d, не закрыто к концу окна %d; клеток с медианой >= интервала выплаты Y: %d; "
         "медиана < интервала: %d (%s); нет закрытых сделок: %d; %sd2_durations.csv"
         % (sum(int(r["closed_positions"]) for r in du), sum(int(r["open_at_end_positions"]) for r in du), med_ok, len(med_bad), "; ".join(med_bad), no_tr, D),
         source="P-37 §5 п.(4), аудит M-01; sel_d2.py")
    setv("d2_no_entry_before_from_min",
         "флаг ядра (по умолчанию ВЫКЛ; a24f4c96): запрет входа и решений правила funding до from_min задания (в разогреве ряда ставок); без флага первая сделка пары терялась как 'до окна' (pre_window). "
         "Применён: калибровка D2 на формировании (sel_d2.py), σ_форм D2 (sel_waves.py, режим form) и команды волны D2 (--no-entry-before-from в каждой команде)",
         status=ASSIGN, source="P-37 §5, §18 п.12 [Н55]; D5 ii; M3-04")
    ap_p = sum(int(r["active_pair_days"]) for r in sg if r["cell"].startswith("D2|"))
    ap_k = sum(int(r["active_pair_days_kernel_d2"]) for r in sg if r["cell"].startswith("D2|") and r["active_pair_days_kernel_d2"])
    setv("d2_active_pair_day_rule",
         "знаменатель 'активные пары-сутки' D2 считает агрегатор по панелям: сутки активны, если open обеих ног конечен хотя бы в одну минуту суток И накоплено >= k выплат ряда d. Реализовано: "
         "d2_active.py (pair_active / group_active); σ_форм D2 на формировании 08-14.10: активных пара-суток по панелям %d против %d по `active` ядра (z окна 60 мин); для торгового окна функцию вызывает "
         "агрегатор F8" % (ap_p, ap_k), status=ASSIGN, source="P-37 §10.1 [Н55, Н27]; D5 iii; M-07, P-02, K-05; d2_active.py")
    setv("f8_d2_active_denominator_note",
         "знаменатель 'активные пары-сутки' из --job-day-out определяется z (window = min_obs = 60 у D2): у разреженных бирж (Kraken, Lighter, Aster, Bitget-акции) z почти не определён, active = 0 при "
         "ненулевом числе сделок; агрегатор печатает trades_on_days_without_active_pairs, сутки без активных пар в ряд не входят; решение r3.2 (D5 iii): знаменатель D2 считает агрегатор по панелям "
         "(d2_active_pair_day_rule); S2: функция d2_active.py готова и применена к σ_форм D2 (панели %d против ядра %d пара-суток формирования); подключение к f8_agg.py - задача F8" % (ap_p, ap_k),
         status=ASSIGN, source="f8_agg.py cells; xdl9-f8-job-lists-2026-11-06.md §7; D5 iii; d2_active.py")
    setv("f8_waves_d2_pairs_fix",
         "в манифесте и wave1.sh S1 команда D2 вызывала d2_diff_funding.py с --pairs на файл клеток, которого у D2 нет (S3 правил только готовый wave1.sh); S2: исправлен ГЕНЕРАТОР "
         "(sel_waves.build_cmd: --pairs = файл пар группы), манифест и wave1.sh пересозданы; списки заданий волн пересозданы целиком и требуют пересборки f8_units.tsv задачей F8",
         status=ASSIGN, source="f8_lists.py unit_main; sel_waves.py (S2)")

    # ---------------------------------------------------------------- каталоги, США / не-США, нуль и отбор A1
    setv("bars_share_threshold_by_catalog",
         "%d каталогов (биржа x полоса), Kneedle по долям валидных минут относительно маски (США-полосы: минуты кассы, остальные: все минуты); %scatalogs.csv колонка threshold_share (sha256 %s)"
         % (len(cat), D, sha(os.path.join(docs, "catalogs.csv"))), source="P-37 §6.4; sel_catalog.py")
    setv("market_24_7_label_by_exchange_class", "по всем (биржа x класс): %s; %scatalogs.csv колонка label_247" % (dict(Counter(r["label_247"] for r in cat)), D),
         source="P-37 §6.7 [Н23]; прокси «оборот в выходные > 0»: есть валидные минуты (v > 0) в выходные формирования")
    dec = meta["us_rule_decided_by"]
    setv("sel_us_nonus_rule_counts",
         "активов классов акции/ETF/индексы %d, не-США %d (по таблице asset_map после D-01); решено меткой биржи %d, запасным правилом по данным %d, по умолчанию (нет данных) %d; метка расходится с данными у %d; "
         "контрактов теста %d; %ssession_assets.csv" % (meta["assets"], meta["nonus_assets"], dec["метка"], dec["данные"], dec["умолчание"], dec["метка_против_данных"], meta["n_contracts"], D),
         source="sel_catalog.py; catalog_meta.json")
    setv("sel_us_nonus_rule",
         "не-США, если у актива есть метка региона не-США из первичных полей бирж (Bybit region=XX, Bitget 'листинг XX', Binance HK|KR|CN_EQUITY, нога в HKD, валюта в имени BingX NCSK..2JPY/KRW/...); "
         "США, если метки есть и все 'US' (Bybit region=US); метка не-США сильнее метки США; ТОЛЬКО для активов без метки (запасное правило): медиана (по основным контрактам актива с >= 300 доходностей) "
         "доли дисперсии доходностей в кассе США < 3900/20160 = 0.1935 - не-США, иначе США (нет данных - США); решение по активу переносится на все биржи (r3.2, D6: метки приоритетны)",
         status=ASSIGN, source="P-37 §9 [Н22, Н56]; sel_catalog.py (S2); asset_class.region_labels / region_verdict")
    n_ag = sum(int(r["agree"]) for r in au)
    flag_by = Counter()
    for r in af:
        flag_by[(r["rule_cause"], r["truth"])] += 1
    lab_n = sum(v for (c, t), v in flag_by.items() if c == "метка биржи")
    lab_ok = flag_by[("метка биржи", "не-США")]
    dat_n = sum(v for (c, t), v in flag_by.items() if c.startswith("данные"))
    dat_ok = sum(v for (c, t), v in flag_by.items() if c.startswith("данные") and t == "не-США")
    dat_us = sum(v for (c, t), v in flag_by.items() if c.startswith("данные") and t == "США")
    dat_adr = sum(v for (c, t), v in flag_by.items() if c.startswith("данные") and t == "США (АДР)")
    lab_known = sum(v for (c, t), v in flag_by.items() if c == "метка биржи" and t in ("не-США", "США", "США (АДР)"))
    setv("us_rule_accuracy",
         "правило США/не-США S2 (sel_us_nonus_rule: метки биржи приоритетны, запасное правило по данным): выборка 40 активов (seed 37; 20 'не-США' + 20 'США' по новому решению): совпало %d (%d %%); "
         "(S1/r3.1 по доле дисперсии с метками Binance/BingX: 31 из 40, 78 %%); все %d активов 'не-США': решено меткой биржи %d - из них с суждением %d, верно 'не-США' у %d; "
         "решено запасным правилом по данным %d - верно 'не-США' у %d, на деле США %d, США (АДР, спорные) %d; выборка суждений - %s"
         % (n_ag, round(100.0 * n_ag / max(1, len(au))), len(af), lab_n, lab_known, lab_ok, dat_n, dat_ok, dat_us, dat_adr, "asset_us_rule_truth.csv (суждение одного читателя)"),
         source="P-37 §9 [Н22, Н56]; asset_us_rule_audit.py; %sus_rule_audit40.csv, us_rule_nonus_flagged.csv, us_rule_label_misses.csv" % D)
    setv("us_rule_priority",
         "метки региона биржи (Bybit region=, 'листинг XX' Bitget и др.; по проверке 27 из 27 верных) имеют приоритет; правило по доле дисперсии в кассе США (порог 3900/20160 = 0.1935) - только запасное; "
         "S2: реализовано в sel_catalog.py (asset_class.region_labels / region_verdict), список не-США, каталоги, панели panels_us и все нижестоящие таблицы пересчитаны; "
         "метка расходится с данными у %d активов; запасное правило решило %d активов 'не-США', верно по суждению у %d" % (dec["метка_против_данных"], dat_n, dat_ok),
         status=ASSIGN, source="P-37 §9 [Н56]; D6; M-08, K-06")
    setv("null_shift_days",
         "крипта: календарные сутки; остальные полосы: торговые дни (позиция pos(d) = число будних суток в [01.10, d), у субботы и воскресенья - позиция следующего понедельника; источник T[pos(d) - k]); "
         "сдвиги k = +1, +2, -1; общее пересечение 03.10-13.10 (сутки 2467-2477): 11 суток, из них будних 7; r3.2 (D13): смещения источника серии p2 / p1 / r / m1 = источник на 2 / 1 / 0 суток раньше / "
         "на сутки позже (все окна внутри формирования 2465-2478)", status=ASSIGN,
         source="P-37 §7.2 [Н38, Н63], отступление F7 (торговый день вне крипты) сохранено; sel_shift.py, тест test_statarb_sel_fdr.py; k = +1 совпадает с правилом F7")
    setv("null_shifts_r3", "{+1,+2,-1} суток для крипты (календарные), для остальных полос - торговые дни (null_shift_days); r3.2: выравнивание ребра {a, b} = упорядоченная пара смещений (o_a, o_b), "
         "o_a != o_b, из {p2, p1, r, m1}: 12 выравниваний на ребро, 6 запусков form на каталог", status=ASSIGN, source="P-37 §7.2 [Н38, Н63]; sel_shift.RUNS")
    setv("null_common_span_days_r3", "11 (03.10-13.10, сутки 2467-2477) для обеих единиц сдвига; требование CEO >= 10 суток выполнено; будних суток 7 (выборка США-полос: 7 касс x 390 мин)",
         status="производное", source="P-37 §7.2: формирование 14 суток, сдвиги +1, +2, -1; sel_shift.check_span")
    setv("null_partitions",
         "3 разбиения (seed 37) задают граф РЕАЛЬНЫХ пар (пара 'внутри половины' хотя бы в одном из них; покрытие ≈ 0.875); нулевых наборов по разбиениям больше нет (r3.2, D13): нуль - 9 повторов "
         "НА ТЕХ ЖЕ РЁБРАХ (статистика той же пары активов при другом выравнивании рядов; у каждого ребра своя случайная перестановка 12 выравниваний, seed 37: повторы 1-9 строят t*, 10-12 - контрольные "
         "для G9); 4-е разбиение в splits.csv зарезервировано (поток случайных чисел как в r3.1), не используется", status=ASSIGN,
         source="P-37 §7.2 [Н6, Н63]; sel_fdr.py, sel_fdrlib.draw_alignments")
    setv("null_units_bases",
         "k0(t), R(t), n0 и M - в единицах различных баз; жадное паросочетание по возрастанию статистики (при равенстве по паре индексов символов), набор = реальные пары каталога ИЛИ один из 9 "
         "нулевых повторов на рёбрах реального графа (паросочетание каждого отдельно, k0 и n0 суммируются); R - принятые реальные пары <= t; торгуются принятые паросочетанием реальные пары <= t* "
         "(каждая база не более одного раза); k0(t*) >= 5 в этих единицах", status=ASSIGN,
         source="P-37 §7.3а [Н48, Н63]; sel_fdrlib.py (правило в коде ДО пересчёта: 328d3ba7; нуль на рёбрах - S2); оптимистичность оценки в синтетике: тест test_statarb_sel_fdr.py")
    setv("null_matching_rule", "жадное паросочетание (не максимальное): префиксное свойство - принятые пары <= t равны жадному результату на подграфе пар <= t; размер полного паросочетания <= s/2",
         status=ASSIGN, source="P-37 §7.3а (iv); sel_fdrlib.greedy_matching")
    sel_c = [r for r in a1 if r["kind"] == "ok"]
    n_strong = [r for r in a1 if r["kind"] == "strong"]
    n_none = [r for r in a1 if r["kind"] == "none"]
    n_few = [r for r in a1 if r["kind"] == "few"]
    tot_sel = sum(int(r["selected"] or 0) for r in a1)
    setv("t_star_by_catalog",
         "%d каталогов с отбором из %d с отчётом (отбор не выполнен: сильные пары есть - %d; пар нет - %d; мало пар - %d); отобрано пар %d; %sa1_catalogs.csv колонки t_star, R_base, k0_base, fdr, "
         "fdr_hi_x3 (sha256 %s); таблица t* +- оценка FDR: %sa1_tstar_table.csv"
         % (len(sel_c), len(a1), len(n_strong), len(n_none), len(n_few), tot_sel, D, sha(os.path.join(docs, "a1_catalogs.csv")), D), source="P-37 §7; sel_fdr.py")
    setv("null_pairs_n0_by_catalog", "%sa1_catalogs.csv колонки n0_base (в единицах баз: сумма размеров паросочетаний 9 повторов) и n0_pairs (рёбер реального графа x 9 повторов)" % D,
         source="P-37 §7")
    setv("real_pairs_M_by_catalog", "%sa1_catalogs.csv колонки M_base (размер паросочетания реальных пар) и M_pairs (различные пары «внутри половины» хотя бы в одном из разбиений 1-3 = рёбра графа)" % D,
         source="P-37 §7")
    setv("coverage_share_by_catalog", "%sa1_catalogs.csv колонка coverage = M_pairs / все пары каталога (ожидаемо 0.875 при 3 разбиениях)" % D, source="P-37 §7 п.2")
    setv("fdr_null_same_edges",
         "нуль A1 строится на ТЕХ ЖЕ рёбрах, что реальные пары: pseudo-null на рёбрах реального графа, 9 повторов с независимыми выравниваниями (12 выравниваний на ребро = 6 пар смещений x кто из двух "
         "символов с первым смещением; у каждого ребра своя случайная перестановка, seed 37; ни одна нулевая статистика не копия другой: доля рёбер с совпавшими статистиками %s); K0_MIN = 5 "
         "(t_star_min_k0) остаётся; сдвиги {+1,+2,-1} и общее пересечение прежние. Реализовано: sel_fdr.py, sel_fdrlib.py, sel_deck_prep.py links, sel_form_all.sh"
         % ("; ".join(sorted({r["null_dup_share"] for r in a1 if r["null_dup_share"]}))), status=ASSIGN, source="P-37 §7.2, §7.3, §18 п.14 [Н63]; D13; M2-01, M2-02")
    setv("fdr_report_range",
         "оценка FDR в отчёте - диапазоном (условная доля ложных x1...3), не '0.06-0.10'; a1_catalogs.csv: fdr (номинал по нулю) и fdr_hi_x3 = min(1, 3 x fdr); синтетика (test_statarb_sel_fdr.py, EndToEnd): "
         "фактическая доля ложных 0.12-0.14 при заявленных 0.10 (60 символов, 12 истинных пар, цены), 0.19 на готовых статистиках",
         status=ASSIGN, source="P-37 §7.3 п.3б, §15.1 [Н63]; D13; L5-03")
    setv("selection_not_executed_report",
         "отчёт различает 'отбор не выполнен: сильные пары есть (R_base = ...)' и 'пар нет'; печатает R_base, k0_base и число пар ниже t* даже при невыполненном отборе; G9 в отчёте - 'не информативен "
         "как калибровка'. S2: сильные пары есть, отбор не выполнен - %d каталогов (%s); пар нет (FDR > 0.10 при любом t) - %d; мало пар (M < 10 или n0 < 9) - %d"
         % (len(n_strong), "; ".join("%s R_base=%s k0_base=%s" % (r["catalog"], r["R_base"], r["k0_base"]) for r in n_strong), len(n_none), len(n_few)),
         status=ASSIGN, source="P-37 §7.3 п.3б, §7.5, §15.1 [Н63]; D13; L5-04, M2-02")
    setv("a1_pair_selection_unit",
         "паросочетание 'база не чаще 1 раза' остаётся (D2): реальный отбор = принятые паросочетанием реальные пары (S2: %s); число пар не строже t* печатается рядом (a1_catalogs.csv pairs_le_tstar); "
         "числа пересчитаны по D13 (S1: 162 пары в 6 каталогах)"
         % "; ".join("%s %s пар из %s не строже t*" % (r["catalog"], r["selected"], r["pairs_le_tstar"]) for r in sel_c),
         status=ASSIGN, source="P-37 §7.3а [Н53]; D2; F-04, K-02, А-11")
    b1 = os.path.join(docs, "b1_baskets.csv")
    setv("baskets_b1", "корзин %d (цели с тремя компонентами по корреляции доходностей формирования), исключено целей %d; %sb1_excluded.csv; %sb1_baskets.csv, sha256 %s"
         % (len(S.read_csv(b1)), len(S.read_csv(os.path.join(docs, "b1_excluded.csv"))), D, D, sha(b1)),
         source="план F7b; sel_b1.py; G9b %sg9b_b1_vs_numpy.csv" % D)
    cnt = Counter(r["kind"] for r in hl)
    not_traded_by = dict(Counter(r["kind"] for r in cells if not r["status"].startswith("торгуется")))
    setv("cells_total", "клеток без рук: %s (C1 - три окна); торгуется z-правило %d (клеток-рук %d, из них копий a1 = a0 %d), D2 %d; не торгуется %d (%s); всего клеток-рук в волнах %d, различных %d"
         % (dict(cnt), len(z_traded), 2 * len(z_traded), n_copies, n_by["D2"], len(cells) - len(traded), not_traded_by, n_hands, n_hands - n_copies),
         source="sel_waves.py; %scells_not_traded.csv" % D)
    sm = [r for r in sg if r["sigma_bps_per_day_per_active_pair"] != "не измерена"]
    setv("sigma_formation_by_cell",
         "%s колонка sigma_formation_bps_day_per_active_pair: измерено %d клеток-рук из %d, не измерена (меньше 5 суточных значений или нет закрытых сделок 08-14.10) %d; ряд = закрытые сделки + "
         "MTM открытых позиций на последних сутках (D10); нормировка - активные пары-сутки (z-виды: --job-day-out; D2: по панелям, D5 iii); %ssigma_formation.csv (sha256 %s)"
         % (src_cells, len(sm), len(sg), len(sg) - len(sm), D, sha(os.path.join(docs, "sigma_formation.csv"))), source="P-37 §6.6 [Н13], аудит M-07/P-02; sel_sigma.py")
    setv("sel_form_window_min_obs",
         "EG-окно 03.10-13.10 (общее пересечение сдвигов, 11 суток, 15840 мин, сутки 2467-2477); form --from-day 2478 --window-days 11 --block-chunks 6 (блок 768 мин, 21 блок, половины 7680 | 8160) "
         "--min-obs 300 (= n_ret_min) --alpha 1e9 (не порог: статистики всех пар); наборов на каталог 7: real + 6 запусков выравниваний (al_*), в каждом по две панели на символ", status=ASSIGN,
         source="P-37 §7.2; sel_form_all.sh; бинарь form tk486@60e61cea не менялся")
    setv("sel_entry_cost_rule",
         "entry_z* = наименьшее ДОСТИЖИМОЕ z сетки (z <= sqrt(W-1)), при котором (1) позиций в пуле клетки на формировании (закрытые сделки + MTM открытых на конце) >= 20 и (2) средний валовый результат "
         "позиции >= 2 x средняя комиссия позиции клетки (12.6 bps при b = 1); нет z с (2) - 'безубыточной точки нет на достижимой части сетки'; (2) только при < 20 позициях - 'мало сделок формирования'; "
         "число не подставляется; 90 %-интервал среднего валового - сутки как кластеры (MTM - в сутки 14.10), t с df = суток с позициями - 1", status=ASSIGN, source="P-37 §6.2 (а)-(в) [Н37, Н39, Н60]; sel_entry.py")
    setv("sel_entry_run",
         "ядро xdl9-a24f4c96 (PGO) с --rearm --mtm-open --cells-out (C1 ещё --delay-x 1), панели ~/xdl9/form (обрезаны по 15.10 00:00), задания 01.10-14.10, exit_z 0, stop_z inf, max_hold = W, "
         "без фандинга; только достижимые z", status=ASSIGN, source="P-37 §6.2, §5 [Н35, Н60]")
    grid_c1 = {m: max(fnum(g["mean_gross_bps"]) for g in eg if g["cell"].startswith("C1|") and g["cell"].endswith("|w%s" % m) and g["mean_gross_bps"]) for m in (4, 16, 64)}
    setv("sel_c1_result",
         "C1: %d клеток (115 пар бирж x класс, по 3 окна); торгуется %d (%s); не торгуется: мало сделок формирования %d, безубыточной точки нет на достижимой части сетки %d, полураспад не измерен %d; "
         "наибольший средний валовый (закрытые + MTM) на достижимом z: w4 %.1f, w16 %.1f, w64 %.1f bps против 25.2 bps (2 x круг 12.6)"
         % (len(ez_c1), len(c1_traded), "; ".join("%s z=%s N=%s валовый %s [%s; %s]" % (r["cell"], r["entry_z"], r["trades_at_z"], r["mean_gross_bps"], r["gross_lo90"], r["gross_hi90"]) for r in c1_traded),
            len(c1_few), sum(1 for r in ez_c1 if r["status"].startswith("не торгуется: безубыточной")), sum(1 for r in ez_c1 if r["status"].startswith("полураспад")),
            grid_c1[4], grid_c1[16], grid_c1[64]),
         status=MEAS, source="%sentry_z.csv, entry_grid.csv; пола W сверх протокола нет" % D)
    setv("sel_d2_funding_g7",
         "G7 пройден в F6 (statarb-xdl9-gates-2026-11-06.md); фандинг формирования - только строки time_ms < 15.10 00:00 (~/xdl9/funding_form, sel_deck_prep.py fundcut: разбирается только метка "
         "времени); ряд d = r_Y - r_X на метках Y - d2_diff_funding.py (масса сходится на каждой паре); интервалы выплат для ориентации - funding_intervals.py по тем же файлам", status=MEAS,
         source="docs/findings/statarb-xdl9-gates-2026-11-06.md; sel_d2.py")
    setv("sel_wave_window",
         "from_day 2479 (15.10) to_day 2500 (05.11 не включая); min-span 1440; флаги: --rearm (кроме D2), --trades-out --trades-ext --trade-jobs всех, --open-out --mtm-open, --cells-out, --ext-out, "
         "--job-ext-out, --job-day-out, --daily-out; D2 - ещё --no-entry-before-from; потоки THREADS (по умолчанию 8)", status=ASSIGN, source="sel_waves.py; P-37 §14, §18")
    setv("sel_wave_binary",
         "~/xdl9/bin/statarb-xdl9-a24f4c96-pgo (tk493-xdl9, PGO-сборка финального исходника a24f4c96, В-32); sha256 %s; не-PGO того же исходника ~/xdl9/bin/statarb-xdl9-a24f4c96 sha256 %s "
         "(выходы побайтно равны); бинарь form - statarb-tk486-60e61cea" % (BIN_PGO_SHA, BIN_PLAIN_SHA), status=ASSIGN, source="count_binary; docs/findings/xdl9-kernel-w5-2026-11-06.md")
    setv("count_binary",
         "~/xdl9/bin/statarb-xdl9-a24f4c96-pgo (Deck); sha256 %s; исходник a24f4c96 (коммит ветки tk493-xdl9); PGO-сборка (В-32, D3); не-PGO того же исходника sha256 %s, выходы побайтно равны; "
         "прежний 4ef45cc8 (sha256 969db927...) заменён: побайтно равен на выключенных флагах (G6а/G6б/G11, xdl9-kernel-w5-2026-11-06.md)" % (BIN_PGO_SHA, BIN_PLAIN_SHA),
         status=ASSIGN, source="P-37 §14, §18 [Н35, Н69]; xdl9-kernel-w5-2026-11-06.md; команда F8: statarb run ... --rearm --trades-ext --trade-jobs <все номера> --ext-out <csv> --job-day-out <csv>")
    # G9
    g9_ok = [r for r in g9 if r["status"] == "отобрано"]
    n_hold_b = sum(int(r["k0_hold_base"]) for r in g9_ok)
    e_hold_b = sum(float(r["k0_hold_exp_base"]) for r in g9_ok)
    n_hold_p = sum(int(r["k0_hold_pairs"]) for r in g9_ok)
    e_hold_p = sum(float(r["k0_hold_exp_pairs"]) for r in g9_ok)
    n_hold_b_all = sum(int(r["k0_hold_base"]) for r in g9)
    e_hold_b_all = sum(float(r["k0_hold_exp_base"]) for r in g9)
    worst_t = max(fnum(r["rel_dt_max"]) for r in g9n if r["rel_dt_max"])
    worst_b = max(fnum(r["rel_dbeta"]) for r in g9n if r["rel_dbeta"])
    setv("sel_gate_g9",
         "G9 (не информативен как калибровка, D13): EG form против numpy на %d парах - %d расхождений (макс. относительное |dt| %.2e, |dbeta| %.2e); нулевой тест на контрольных выравниваниях рёбер "
         "(повторы 10-12 из 12; те же ряды, что у обучающих, - не независимый тест): каталоги с отбором: прошло t* %d против ожидаемых %.1f в единицах баз, %d против %.1f в единицах пар; все каталоги с t*: %d "
         "против %.1f в единицах баз" % (len(g9n), sum(1 for r in g9n if r["status"] != "ok"), worst_t, worst_b, n_hold_b, e_hold_b, n_hold_p, e_hold_p, n_hold_b_all, e_hold_b_all),
         status=MEAS, source=D + "g9_eg_vs_numpy.csv, g9_null_test.csv")
    setv("g9_status_result",
         "не информативен как калибровка: прошло %d, ожидалось %.1f (единицы баз) и %d, %.1f (единицы пар) по сумме каталогов с отбором; словом 'пройден' не называется (правило g9_status_rule, D13)"
         % (n_hold_b, e_hold_b, n_hold_p, e_hold_p), status=MEAS, source="правило g9_status_rule; %sg9_null_test.csv" % D)
    setv("sel_gate_g9b", "корзины B1: %d целей, %d расхождений (макс. |d corr| %s)" % (len(g9b), sum(1 for r in g9b if r["status"] != "ok"), max(r["max_dcorr"] for r in g9b)),
         status=MEAS, source=D + "g9b_b1_vs_numpy.csv")
    setv("sel_wave_controls", "волна 4 (wave4.sh): контроль §11.8 «все пары каталога без отбора» для торгуемых клеток A1 (%d клеток); задания пишет sel_allpairs_jobs.py на Deck; контроли §11.1-11.4 "
         "и срез «без 17» §11.9 - списки F8 (S3), пересобираются после S2" % n_by["A1"], status=ASSIGN, source="P-37 §11.8, §18 п.4, п.8")
    # ожидаемое число ложных клеток и первая строка отчёта
    nz = n_z_total
    n_unt = nz - len(z_traded)
    setv("expected_false_candidates",
         "0.05 x M_eff ... 0.05 x M; M - число торгуемых клеток-рук после слияния a1 == a0 (r3.2, D9; S2: %d клеток-рук, %d копий, M = %d; M_eff - по матрице корреляций после счёта); клетки с a1 "
         "тождественной a0 (стоп недостижим) считаются один раз (колонка a1_equals_a0); печатается size x M из size_test.py (xdl9-stats-w5-2026-11-06.md)" % (n_hands, n_copies, n_hands - n_copies),
         status=ASSIGN, source="P-37 §10.4 [Н18, Н43, Н59]; M = число клеток семьи, M_eff - эффективное число независимых клеток; D9, G-04, K-11")
    a1_cells = [r for r in z_traded if r["kind"] == "A1"]
    a1_pairs_traded = sum(int(r["hl_pairs_ok"] or 0) for r in a1_cells)
    setv("report_first_line",
         "первая строка отчёта (P-37 §0.8: сначала и-н, затем а-з): ОХВАТ - C1 торгуется %d клеткой из %d (кросс-биржа почти не проверена по построению, а не 'нет края'); %d из %d z-клеток отсеяны "
         "на формировании (вариант «закрытые + MTM»); A1 в %d каталогах из %d (отобрано %d пар, торгуется %d; отбор не выполнен при сильных парах в %d, пар нет в %d, мало пар в %d); %d клеток-рук, из них %d - "
         "копии (a1 = a0); точность правила США/не-США %d %% (%d из %d; метки биржи верны %d из %d активов с суждением (всего решено меткой %d), запасное правило по данным - %d из %d 'не-США' с суждением или без); плацебо-основное не построено; одна фаза рынка, "
         "35 суток, выжившие, нет стакана, нет проскальзывания, нет Break (В-92); delay-0 - 'цена последней сделки'; затем 'чего тест не показывает': индексы, фиат, HK-бумаги - 'нет данных', не 'не ясно'; "
         "независимость по 12 символам Binance, D2 Binance x Bybit и парам мажоров от чтений P&L окна до params (report_independence_warning); числа - после пересчёта по r3.2 (S2, 07.11)"
         % (len(c1_traded), len(ez_c1), n_unt, nz, len(sel_c), len(a1), tot_sel, a1_pairs_traded, len(n_strong), len(n_none), len(n_few), n_hands, n_copies,
            round(100.0 * n_ag / max(1, len(au))), n_ag, len(au), lab_ok, lab_known, lab_n, dat_ok, dat_n),
         status=ASSIGN, source="P-37 §0.8, §15 [Н41, Н45, Н67], M-09, M4-03; D18")
    setv("params_state",
         "S2 07.11: значения отбора пересчитаны по П-37 r3.2 (D1, D2, D5 i-iii, D6, D10, D13, D14): метки региона биржи приоритетны (D6), нуль A1 на рёбрах реального графа (D13), HL лаг 1 и лаг 15 (D14), "
         "entry_z и σ_форм по «закрытые + MTM» (D10), D2 с ориентацией по интервалу выплат и флагом ядра (D5), ядро a24f4c96 PGO; коммит ДО торгового счёта (G0 часть 3); хеши таблиц - по git-блобам (A-27). "
         "Строки f8_* (S3) относятся к спискам волн S1 и пересобираются задачей F8. История: S1 06.11 (r3.1, 8b79f097); F7 (58af894f) - r2, в docs/archive/xdl9-selection-r2-2026-11-06/; "
         "отчёт и таблицы S1 - docs/archive/xdl9-selection-s1-2026-11-06*",
         status=SRC, when=WHEN, source="")
    # ссылки на заменённые таблицы в нетронутых S2 строках: F7 -> архив r2, S1 -> архив S1
    for r in rows:
        if r["when"] == WHEN or "docs/findings/xdl9-selection-2026-11-06/" not in r["value"] + r["source"]:
            continue
        tgt = S1A if "S1" in r["when"] else R2
        r["value"] = r["value"].replace("docs/findings/xdl9-selection-2026-11-06/", tgt)
        r["source"] = r["source"].replace("docs/findings/xdl9-selection-2026-11-06/", tgt)
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
    print("строк", len(rows), dict(Counter(r["status"] for r in rows)), "G9 hold", n_hold_b, e_hold_b)


if __name__ == "__main__":
    main()
