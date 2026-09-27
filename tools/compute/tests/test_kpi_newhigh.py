"""kpi-newhigh.py: Каплан-Мейер для цензурированного времени ожидания перехая (Судья 1d2a3f4, 27.09) и
доля часов «до перехая»/просадки под водой (Судья e6386a1, 27.09) — известный ряд -> ожидаемая доля/квантиль.

Запуск: `python -m pytest tools/compute/tests -q` (на Windows вывод кириллицы — PYTHONIOENCODING=utf-8).
"""
import importlib.util
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(HERE, "..", "kpi-newhigh.py")


def load():
    spec = importlib.util.spec_from_file_location("kpi_newhigh", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


kn = load()


def test_km_quantile_known_series():
    # 4 события в 1,2,3,4 + цензура в 5 (n=5): S(1)=0.8, S(2)=0.6, S(3)=0.4, S(4)=0.2; в 5 выбывает цензурой,
    # «смерти» нет -- дожитие остаётся 0.2 до конца ряда.
    pairs = [(1.0, False), (2.0, False), (3.0, False), (4.0, False), (5.0, True)]
    med, med_c = kn.km_quantile(pairs, 0.5)
    assert med == pytest.approx(3.0) and med_c is False  # S(3)=0.4 <= 1-0.5 первым
    p90, p90_c = kn.km_quantile(pairs, 0.9)
    assert p90 == pytest.approx(5.0) and p90_c is True  # дожитие не опускается до 0.1 -- граница, не оценка


def test_km_quantile_no_censoring_matches_plain_quantile():
    # без цензуры К-М должен совпасть с обычной эмпирической функцией дожития
    pairs = [(float(x), False) for x in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10)]
    med, med_c = kn.km_quantile(pairs, 0.5)
    assert med == pytest.approx(5.0) and med_c is False  # S(5)=0.5 <= 0.5


def test_km_quantile_empty():
    assert kn.km_quantile([], 0.5) == (None, False)


def test_rolling_kpi_frac_gt_h_and_censoring():
    """Единственное закрытие в первый час августа, дальше без сделок: почти всё время месяца -- цензура (перехая
    не было до конца данных 24.09). Главный показатель должен показывать это как «ждать > H» почти везде, а не
    занижать долю подстановкой цензуры вместо истинного (неизвестного) ожидания."""
    s0 = kn.ms("2026-08-01")
    closes = [(s0 + kn.MS_H, 100.0)]
    roll = kn.rolling_kpi(closes)
    aug, sep = roll["aug"], roll["sep"]
    # календарная сетка не зависит от данных: 31 сутки августа целиком видны (t <= 24.09 - H сут; по умолчанию
    # H = 5 -- правило П-07, Судья 3d3a5f6 -- отсечка 19.09); в сентябре -- по 19.09 00:00 включительно (18 суток + 1 час)
    assert aug["n_main"] == 31 * 24
    assert sep["n_main"] == 18 * 24 + 1
    # единственная точка «жду <= H» в августе -- самый первый час (закрытие +100 приходит через 1 ч)
    assert aug["frac_gt_h"] == round((31 * 24 - 1) / (31 * 24), 3)
    # весь сентябрь идёт после единственного закрытия -- везде цензура (кроме ровно точки отсечки, где
    # оставшееся время == H, не «> H»)
    assert sep["frac_gt_h"] == round((18 * 24) / (18 * 24 + 1), 3)
    # максимум месяца в августе так и не обновился за время данных -- это нижняя граница, не число
    assert aug["max"]["max_censored"] is True
    assert aug["max"]["median_censored"] is True
    assert aug["max"]["p90_censored"] is True
    # доля цензуры считается по всем часам месяца (не только вошедшим в главный показатель)
    assert aug["max"]["cens"] == round(743 / 744, 3)


def test_rolling_kpi_h_days_param_changes_window():
    """H -- параметр (Судья e6386a1: для П-07 H = 5 суток, не 7 по умолчанию)."""
    s0 = kn.ms("2026-08-01")
    closes = [(s0 + kn.MS_H, 100.0)]
    roll7 = kn.rolling_kpi(closes, h_days=7)
    roll5 = kn.rolling_kpi(closes, h_days=5)
    assert roll7["aug"]["h_hours"] == 7 * 24
    assert roll5["aug"]["h_hours"] == 5 * 24
    # более короткое H отодвигает отсечку в сентябре ближе к концу данных -- строго больше t входит в главный показатель
    assert roll5["sep"]["n_main"] > roll7["sep"]["n_main"]


def test_drawdown_stats_hourly_survives_month_boundary():
    """Откат, начавшийся в конце августа и продолжающийся в сентябре, должен быть виден в обоих месяцах по часовой
    кривой -- это и есть исправление Судьи e6386a1 п.2.1 (группировка по месяцу пика теряла глубину другого месяца)."""
    s0 = kn.ms("2026-08-01")
    day = 24 * kn.MS_H
    closes = [
        (s0 + 1 * kn.MS_H, 100.0),                      # пик +100 в августе
        (s0 + 30 * day, -80.0),                         # просадка в последний день августа (30.08)
        (s0 + 33 * day, -10.0),                         # дно в сентябре (03.09) -- глубина $90 от пика
        (s0 + 40 * day, 200.0),                         # новый пик -- откат закрыт
    ]
    dd = kn.drawdown_stats(closes)
    # по старому определению (месяц пика) вся глубина уходит в август -- сентябрь её не видит вовсе
    assert dd["sep"]["depth_max"] is None
    # почасовая кривая видит просадку в обоих месяцах: в августе глубина доходит до $80 (первая просадка 30.08),
    # в сентябре -- до $90 (дно 03.09, пик $100, дно $10) -- то, что старое определение в сентябре теряло совсем
    assert dd["aug"]["hourly"]["max_usd"] == pytest.approx(80.0, abs=0.5)
    assert dd["sep"]["hourly"]["max_usd"] == pytest.approx(90.0, abs=0.5)
    assert dd["sep"]["hourly"]["max_usd"] > (dd["sep"]["depth_max"] or 0)


def test_drawdown_stats_hourly_uses_d_usd_threshold():
    """В-123 (владелец, 27.09): порог показа стороны падения -- 2 % депозита $2500 = $50 (не $25, слово владельца,
    меняемо), поле называется `frac_gtD` и несёт свой порог `d_usd` -- дашборд не должен опираться на magic-число 25."""
    s0 = kn.ms("2026-08-01")
    day = 24 * kn.MS_H
    closes = [(s0 + 1 * kn.MS_H, 100.0), (s0 + 5 * day, -30.0)]  # просадка $30: между $25 (старый порог) и $50 (новый)
    dd = kn.drawdown_stats(closes)
    assert dd["aug"]["hourly"]["d_usd"] == kn.FALL_SHOW_D_USD == 50.0
    assert dd["aug"]["hourly"]["frac_gtD"] == 0.0  # $30 не глубже $50 -- новый порог не считает это "глубоко"
    assert dd["aug"]["hourly"]["frac_gt0"] > 0  # но это по-прежнему просадка > $0


def test_stability_by_day_worst_case_ge_point():
    """Исключение любых одних суток не может улучшить долю сильнее, чем убрать день, который единственный сдвигал
    её вниз -- на простом ряду с одним закрытием исключение того самого дня обязано дать 0 (перехай сразу, часов
    ожидания нет вовсе -- частный случай), а необязательно совпадает с точкой."""
    s0 = kn.ms("2026-08-01")
    closes = [(s0 + kn.MS_H, 100.0), (s0 + 10 * 24 * kn.MS_H, 50.0)]
    worst = kn.stability_by_day(closes, h_days=5)
    base = kn.rolling_kpi(closes, h_days=5)
    # худший случай (максимум по исключениям) не может быть строго лучше точки -- исключать нечего в дни без сделок
    assert worst["aug"] >= (base["aug"]["frac_gt_h"] or 0.0) - 1e-9


def test_stability_by_symbol_no_map_returns_none():
    """Нет карты монет для этого ряда (или она не покрывает все закрытия) -- `None`, не число по частичному
    подмножеству: так устойчивость по монете сейчас есть только у главного варианта (data/t32/main-trades.csv)."""
    s0 = kn.ms("2026-08-01")
    closes = [(s0 + kn.MS_H, 100.0), (s0 + 2 * kn.MS_H, -30.0)]
    r = kn.stability_by_symbol(closes, {})
    assert r == {"aug": None, "sep": None}
    r2 = kn.stability_by_symbol(closes, {s0 + kn.MS_H: "BTCUSDT"})  # покрыт только один из двух
    assert r2 == {"aug": None, "sep": None}
    r3 = kn.stability_by_symbol(closes, {s0 + kn.MS_H: "BTCUSDT", s0 + 2 * kn.MS_H: "BTCUSDT"})  # покрыт, одна монета
    assert r3["aug"] is not None


def test_verdict_kpi_pass_fail_edge():
    """Судья 3d3a5f6, правило 2: точка + устойчивость (без бутстреп-порога)."""
    ok = {"aug": 0.05, "sep": 0.05}
    v, states, note = kn.verdict_kpi(ok, {"aug": 0.08, "sep": 0.09}, {"aug": 0.07, "sep": 0.08})
    assert v == "проходит" and states == {"aug": "OK", "sep": "OK"}
    bad = {"aug": 0.42, "sep": 0.15}
    v2, states2, _ = kn.verdict_kpi(bad, {"aug": 0.5, "sep": 0.2}, {"aug": None, "sep": None})
    assert v2 == "не проходит"
    edge = {"aug": 0.12, "sep": 0.05}
    v3, states3, _ = kn.verdict_kpi(edge, {"aug": 0.08, "sep": 0.06}, {"aug": None, "sep": None})
    # база > 0,10 в августе, но устойчивость (макс. по исключениям) опускает <= 0,10 -- не FAIL и не OK -> граница
    assert v3 == "на границе" and states3["aug"] == "EDGE"
    none_case = {"aug": None, "sep": 0.05}
    v4, _, _ = kn.verdict_kpi(none_case, {"aug": None, "sep": 0.0}, {"aug": None, "sep": None})
    assert v4 == "нет данных"


def test_drawdown_stats_sensitivity_and_episode_list_present():
    s0 = kn.ms("2026-08-01")
    day = 24 * kn.MS_H
    closes = [(s0 + 1 * kn.MS_H, 100.0), (s0 + 5 * day, -50.0), (s0 + 10 * day, 30.0), (s0 + 12 * day, -60.0)]
    dd = kn.drawdown_stats(closes)
    assert set(kn.SENS_GRID) == {(10, 0.2), (5, 0.2), (20, 0.2), (10, 0.1), (10, 0.3)}
    for pk in ("aug", "sep"):
        assert "dmin10_rb0.2" in dd[pk]["sensitivity"]
        for row in dd[pk]["episodes"]:
            assert set(row) >= {"t_peak", "t_low", "t_rec", "depth_usd", "relows", "fall_hours", "rec_hours", "open"}
