#!/usr/bin/env python3
"""TK-493 F7 (G0, часть 2): подставляет в docs/research/P-37-params-2026-11-06.csv значения, измеренные на ФОРМИРОВАНИИ (клетки, каталоги, отбор A1,
корзины B1, D2, σ_форм) и добавляет строки решений F7. Коммит файла - ДО торгового счёта. Формат строки: key,value,status,when,source (CRLF как в файле).
"""
import csv, hashlib, io, os, sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

PARAMS = os.path.join(S.ROOT, "docs", "research", "P-37-params-2026-11-06.csv")
CELLS = os.path.join(S.ROOT, "docs", "research", "P-37-params-cells-2026-11-06.csv")
D = "docs/findings/xdl9-selection-2026-11-06/"
MEAS = "измерено на формировании, подтвердить"
ASSIGN = "назначено без Судьи, подтвердить"
WHEN = "F7 06.11"
CRLF = chr(13) + chr(10)


def sha(p, n=12):
    with open(p, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()[:n]


def main():
    with open(PARAMS, "rb") as fh:
        raw_lines = fh.read().decode("utf-8").split(CRLF)
    rows = S.read_csv(PARAMS)
    by = {r["key"]: r for r in rows}
    orig = {r["key"]: (raw_lines[i + 1], (r["value"], r["status"], r["when"], r["source"])) for i, r in enumerate(rows)}   # i-я строка данных = (i+1)-я строка файла
    cells = S.read_csv(CELLS)
    traded = [r for r in cells if r["status"].startswith("торгуется")]
    cs = sha(CELLS)
    docs = S.DOCS

    def setv(key, value, status=MEAS, when=WHEN, source=None):
        if key in by:
            r = by[key]
            r["value"], r["status"], r["when"] = value, status, when
            if source:
                r["source"] = source
        else:
            r = dict(key=key, value=value, status=status, when=when, source=source or "")
            rows.append(r)
            by[key] = r

    cat = S.read_csv(os.path.join(docs, "catalogs.csv"))
    a1 = S.read_csv(os.path.join(docs, "a1_catalogs.csv"))
    d2 = S.read_csv(os.path.join(docs, "d2_params.csv"))
    sg = S.read_csv(os.path.join(docs, "sigma_formation.csv"))
    src_cells = "docs/research/P-37-params-cells-2026-11-06.csv (sha256 %s)" % cs
    n_by = Counter(r["kind"] for r in traded)
    setv("halflife_median_min_by_cell", "%d клеток; %s; колонка hl_median_min (пары с phi вне (0,1) в медиану не входят)" % (len(cells), src_cells),
         source="P-37 §6.1; sel_cells.py")
    setv("window_min_by_cell", "%s; колонка window (W = max(2, round(4 x медиана HL)); пол 2 - минимум ядра min_obs >= 2)" % src_cells,
         source="P-37 §5; sel_cells.py")
    setv("entry_z_by_cell", "торгуется %d клеток (A1 %d, A2 %d, B1 %d); не торгуется C1 %d из %d; %s; колонки entry_z, stop_z_a1; сетка 1.00-4.00 шаг 0.25"
         % (len(traded), n_by["A1"], n_by["A2"], n_by["B1"], sum(1 for r in cells if r["kind"] == "C1"), sum(1 for r in cells if r["kind"] == "C1"), src_cells),
         source="P-37 §6.2; sel_entry.py; %sentry_z.csv, entry_grid.csv" % D)
    setv("max_hold_min_by_cell", "%s; колонка max_hold (z-правило: = W; D2: hold_payments x interval_h x 60)" % src_cells, source="P-37 §5, §6.1; sel_waves.py")
    setv("phi_outside_unit_share_by_cell", "%s; колонки phi_outside_unit / hl_pairs_ok / pairs" % src_cells, source="P-37 §6.1")
    setv("funding_k_by_cell", "D2: %d клеток, k=1:%d k=3:%d k=6:%d; %sd2_params.csv (sha256 %s)"
         % (len(d2), sum(1 for r in d2 if r["k"] == "1"), sum(1 for r in d2 if r["k"] == "3"), sum(1 for r in d2 if r["k"] == "6"), D,
            sha(os.path.join(docs, "d2_params.csv"))), source="P-37 §6.3 [Н21]; sel_d2.py")
    setv("funding_hold_payments_by_cell", "D2: %sd2_params.csv колонка hold_payments (медиана длин эпизодов знака разности ставок в час, в выплатах Y)" % D,
         source="P-37 §6.3 [Н21]; sel_d2.py")
    setv("bars_share_threshold_by_catalog",
         "%d каталогов (биржа x полоса), Kneedle по долям валидных минут относительно маски (США-полосы: минуты кассы, остальные: все минуты); %scatalogs.csv колонка threshold_share (sha256 %s)"
         % (len(cat), D, sha(os.path.join(docs, "catalogs.csv"))), source="P-37 §6.4; sel_catalog.py")
    setv("market_24_7_label_by_exchange_class", "по всем (биржа x класс): %s; %scatalogs.csv колонка label_247" % (dict(Counter(r["label_247"] for r in cat)), D),
         source="P-37 §6.7 [Н23]; прокси «оборот в выходные > 0»: есть валидные минуты (v > 0) в выходные формирования")
    sel_c = [r for r in a1 if r["status"] == "отобрано"]
    setv("t_star_by_catalog", "%d каталогов с отбором из %d; %sa1_catalogs.csv колонка t_star (sha256 %s)"
         % (len(sel_c), len(a1), D, sha(os.path.join(docs, "a1_catalogs.csv"))), source="P-37 §7; sel_fdr.py")
    setv("null_pairs_n0_by_catalog", "%sa1_catalogs.csv колонка n0 (сумма по разбиениям 1-3; A x B, партнёр B сдвинут)" % D, source="P-37 §7")
    setv("real_pairs_M_by_catalog", "%sa1_catalogs.csv колонка M (различные пары «внутри половины» хотя бы в одном из разбиений 1-3)" % D, source="P-37 §7")
    setv("coverage_share_by_catalog", "%sa1_catalogs.csv колонка coverage = M / все пары каталога (ожидаемо 0.875 при 3 разбиениях)" % D, source="P-37 §7 п.2")
    b1 = os.path.join(S.SEL, "b1_baskets.csv")
    setv("baskets_b1", "корзин %d (цели с тремя компонентами по корреляции доходностей формирования), исключено целей %d; %sb1_excluded.csv; b1_baskets.csv в git (%sb1_baskets.csv), sha256 %s"
         % (len(S.read_csv(b1)), len(S.read_csv(os.path.join(docs, "b1_excluded.csv"))), D, D, sha(b1)),
         source="план F7b; sel_b1.py; G9b %sg9b_b1_vs_numpy.csv" % D)
    cnt = Counter(r["kind"] for r in cells)
    d2n = sum(1 for r in cells if r["kind"] == "D2" and r["status"].startswith("торгуется"))
    setv("cells_total", "клеток (вид x биржа/пара x полоса, руки не считаны): %s; торгуется z-правило %d (клеток-рук %d), D2 %d; всего клеток-рук в волнах %d"
         % (dict(cnt), len(traded), 2 * len(traded), d2n, 2 * len(traded) + d2n), source="sel_waves.py; %scells_not_traded.csv" % D)
    setv("sigma_formation_by_cell", "docs/research/P-37-params-cells-2026-11-06.csv колонка sigma_formation_bps_day: измерено %d клеток-рук, не измерена (нет закрытых сделок 08-14.10) %d; %ssigma_formation.csv (sha256 %s)"
         % (sum(1 for r in sg if r["sigma_bps_per_day_per_pair"] != "не измерена"), sum(1 for r in sg if r["sigma_bps_per_day_per_pair"] == "не измерена"), D,
            sha(os.path.join(docs, "sigma_formation.csv"))), source="P-37 §6.6 [Н13]; sel_sigma.py")
    setv("null_shift_days", "crypto: +1 календарные сутки; остальные полосы (акции, ETF, индексы, металлы, энергия, фиат): +1 торговый день (пн <- пт, вс <- пт, иначе -1 сутки)",
         status=ASSIGN,
         source="отступление от P-37 §7 п.2 («+1 сутки»): календарный сдвиг у недельных рынков оставляет у нуля 80 %% перекрытия и делает нуль слишком мягким для вырожденных пар (SGOV, BIL: 619 ложных пар в BingX eq_us); отчёт §3")
    setv("null_partitions", "3 для порога t* + 1 контрольное (нулевой тест G9)", status=ASSIGN, source="P-37 §7 [Н6]; контрольное разбиение добавлено в F7")
    new = [
        ("sel_us_nonus_rule",
         "не-США, если: Binance underlyingType HK/KR/CN_EQUITY; нога HKD; BingX NCSK..2JPY/KRW/...; ИЛИ медиана (по основным контрактам актива с >= 300 доходностей) доли дисперсии доходностей в кассе США < 3900/20160 = 0.1935 (доля минут кассы в неделе); решение по активу переносится на все биржи; остальные = США",
         ASSIGN, "P-37 §9 [Н22]: список не-США «в манифесте» не был построен в F4/F5; %ssession_assets.csv (614 активов, не-США 66)" % D),
        ("sel_session_mask_panels",
         "США-контракты: панели ~/xdl9/panels_us (NaN вне кассы 13:30-20:00 UTC до 31.10, 14:30-21:00 с 01.11; 25 сессий x 390 = 9750 минут); формирование и торговля читают их; остальные панели без маски",
         ASSIGN, "P-37 §9; sel_deck_prep.py mask"),
        ("sel_form_window_min_obs",
         "EG-окно 02.10-14.10 (13 суток, 18720 мин); form --window-days 13 --block-chunks 6 (блок 768 мин, половины 9216 | 9504) --min-obs 300 (= n_ret_min) --alpha 1e9 (не порог: статистики всех пар)",
         ASSIGN, "P-37 §7 п.2; sel_form_all.sh; бинарь form tk486@60e61cea не менялся"),
        ("sel_pair_statistic", "t = max(t_first, t_second) в лучшем направлении (эквивалентно p = max(p1, p2) из select.rs без усечения p)", ASSIGN, "P-37 §7 п.3; sel_fdr.py"),
        ("sel_catalog_min_symbols", "4 (меньше - нулевых пар нет: каталог без отбора); FDR при M < 10 или n0 < 9 - «нет отбора»", ASSIGN, "P-37 §7 п.3; sel_catalog.py, sel_fdr.py"),
        ("sel_hl_spread", "A1/A2/B1: ln y - a - b ln x (a, b - МНК по общим минутам формирования), C1/D2: ln y - ln x; AR(1) по соседним валидным минутам, >= 300 пар наблюдений",
         ASSIGN, "P-37 §6.1; sel_cells.py"),
        ("sel_entry_cost_rule",
         "entry_z* = наименьшее z сетки, при котором средний валовый результат сделки >= 2 x средняя комиссия сделки клетки (ядро: 12.6 bps при b = 1) и есть хотя бы одна сделка; число сделок на выбранном z пишется (минимума нет)",
         ASSIGN, "P-37 §6.2: «круг по round_trip_cost_bps ядра»; sel_entry.py"),
        ("sel_entry_run", "ядро xdl9-02c6a0df (C1 с --delay-x 1), панели ~/xdl9/form (обрезаны по 15.10 00:00), задания 01.10-14.10, exit_z 0, stop_z inf, max_hold = W, без фандинга",
         ASSIGN, "P-37 §6.2"),
        ("sel_c1_result",
         "C1: все 115 клеток не торгуются (114: безубыточной точки на формировании нет; 1: полураспад не измерен); медиана HL спреда между биржами 0.61 мин, W = max(2, 4 x HL) = 2 мин в большинстве клеток; средний валовый на z = 1.00 по клеткам: медиана 1.8 bps (от -0.5 до 5.0) против комиссии 12.6 bps; проба W = 60 и 240 (информационно, не применено): безубыточная точка у 1 клетки из 115 (4 сделки)",
         MEAS, "%sc1_probe.csv, entry_grid.csv; решение о W для C1 - CEO / Судья" % D),
        ("sel_d2_max_hold", "hold_payments x медианный interval_h Y x 60 минут", ASSIGN,
         "P-37 не задаёт max_hold для D2; буквальное 4 x HL (минуты) не позволяет держать позицию через выплату; sel_d2.py"),
        ("sel_d2_window_min_obs", "60 / 60 / refresh 60 (ядром при правиле funding не используются; прогрев)", ASSIGN, "sel_waves.py"),
        ("sel_d2_funding_g7",
         "G7 пройден в F6 (statarb-xdl9-gates-2026-11-06.md: 135 сделок D2 пересчитаны по сырью без расхождений, годовая ставка BTC 5.25-10.55 % на 9 биржах); справочно по формированию (g7_btc_annual.csv, mean(rate/interval_h) x 8760): 2.3-7.4 % на 8 минутных биржах",
         MEAS, D + "g7_btc_annual.csv; docs/findings/statarb-xdl9-gates-2026-11-06.md"),
        ("sel_a2_anchor", "основной контракт актива BTC биржи в каталоге A (A2 только крипта, все остальные члены каталога против него)", ASSIGN, "P-37 §5; sel_cells.py"),
        ("sel_b1_rule",
         "цель + 3 компоненты с наибольшей корреляцией доходностей формирования внутри каталога (биржа x полоса), n_ret >= 300 с каждой, общих минут с доходностью у четырёх ног >= 300; без дублей по базе (основные контракты - один на актив)",
         ASSIGN, "P-37 §5 / план F7b; sel_b1.py"),
        ("sel_wave_window", "from_day 2479 (15.10) to_day 2500 (05.11 не включая); min-span 1440; threads 8; --cells-out --mtm-open --trades-out", ASSIGN, "sel_waves.py"),
        ("sel_wave_binary", "~/xdl9/bin/statarb-xdl9-02c6a0df (tk493-xdl9, без PGO); итоговый PGO-бинарь выбирает CEO после G5/G6", ASSIGN,
         "docs/findings/xdl9-run-extension-2026-11-06.md"),
        ("sel_gate_g9",
         "form против numpy на 30 парах: 0 расхождений, макс. относительное |dt| 6.3e-11, |dbeta| 7.2e-15; нулевой тест (контрольное разбиение 4): прошло t* 24 из 248723 нулевых пар, ожидание по обучающему нулю 10.6",
         MEAS, D + "g9_eg_vs_numpy.csv, g9_null_test.csv"),
        ("sel_gate_g9b", "корзины B1: 10 целей, 0 расхождений (макс. |d corr| 4.6e-7)", MEAS, D + "g9b_b1_vs_numpy.csv"),
    ]
    for k, v, status, src in new:
        setv(k, v, status=status, source=src)
    setv("params_state", "заготовка r2 дополнена F7 06.11: значения формирования подставлены (клетки, каталоги, отбор A1, B1, D2, σ_форм); коммит ДО торгового счёта (G0)",
         status="из источника", when="F7 06.11")
    with open(PARAMS, "w", encoding="utf-8", newline="") as fh:
        fh.write(raw_lines[0] + CRLF)
        for r in rows:
            cur = (r["value"], r["status"], r["when"], r["source"])
            if r["key"] in orig and orig[r["key"]][1] == cur:
                fh.write(orig[r["key"]][0] + CRLF)        # строка не менялась - байт в байт как была
            else:
                buf = io.StringIO()
                csv.writer(buf, lineterminator="").writerow([r["key"], *cur])
                fh.write(buf.getvalue() + CRLF)
    print("строк", len(rows), dict(Counter(r["status"] for r in rows)))


if __name__ == "__main__":
    main()
