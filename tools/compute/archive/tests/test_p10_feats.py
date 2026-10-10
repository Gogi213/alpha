"""p10-feats.py / p10-cov.py (TK-024): признаки H–S на синтетике, семья N, блок Г-120, Г-89, охват.

Запуск: `python -m pytest tools/compute/tests/test_p10_feats.py -q` (кириллица — PYTHONIOENCODING=utf-8).
"""
import csv
import importlib.util
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MIN = 60_000
T0_DAY = 1785715200 * 10 ** 9  # 2026-08-03 00:00 UTC, нс


def load(name, alias):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(HERE, "..", name))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


F = load("p10-feats.py", "p10f_t")
COV = load("p10-cov.py", "p10cov_t")
LC = F.lc


def bars(closes, start=0, up=0.0, down=0.0, vol=1.0):
    b = LC.Bars([])
    for i, c in enumerate(closes):
        b.d[start + i * MIN] = (c + up, c - down, c, vol)
    b.k = sorted(b.d)
    return b


def last_m(closes, start=0):
    return start + (len(closes) - 1) * MIN


# ---------- признаки по свечам ----------

def test_h_abs_coin_minus_btc():
    n = 200
    coin, btc = [100.0] * n, [200.0] * n
    coin[-1], btc[-1] = 101.0, 201.0  # монета +100 bps, BTC +50 bps
    assert F.f_h(bars(coin), bars(btc), last_m(coin)) == pytest.approx(50.0)
    btc[-1] = 202.0  # BTC +100 bps → |100 − 100| = 0
    assert F.f_h(bars(coin), bars(btc), last_m(coin)) == pytest.approx(0.0, abs=1e-9)
    coin[-1] = 99.0  # монета −100, BTC +100 → модуль разности 200
    assert F.f_h(bars(coin), bars(btc), last_m(coin)) == pytest.approx(200.0)


def test_i_efficiency_ratio():
    line = [100.0 + i for i in range(100)]  # прямая вверх → ER = 1
    assert F.f_i(bars(line), last_m(line)) == pytest.approx(1.0)
    zig = [100.0 + (i % 2) for i in range(100)]  # 100,101,100,… → |Δ| за 60 мин = 0 или 1, путь = 60
    c = bars(zig)
    m = last_m(zig)  # i=99 → 101; close_{t−60} = i=39 → 101 → ER = 0
    assert F.f_i(c, m) == pytest.approx(0.0)
    c.d.pop(m - 10 * MIN)
    c.k = sorted(c.d)
    assert F.f_i(c, m) is None  # не хватает бара в окне 61 → признака нет
    flat = bars([100.0] * 100)
    assert F.f_i(flat, last_m([0] * 100)) is None  # путь 0


def test_j_high5_vs_arm_price():
    n = 100
    c = bars([100.0] * n, up=1.0)  # high = 101
    assert F.f_j(c, last_m([0] * n), 100.0) == pytest.approx(100.0)  # (101 − 100)/100 = 100 bps
    c.d[last_m([0] * n) - 5 * MIN] = (200.0, 99.0, 100.0, 1.0)  # вне окна из 5 баров — не влияет
    assert F.f_j(c, last_m([0] * n), 100.0) == pytest.approx(100.0)
    c.d[last_m([0] * n) - 4 * MIN] = (102.0, 99.0, 100.0, 1.0)  # в окне
    assert F.f_j(c, last_m([0] * n), 100.0) == pytest.approx(200.0)
    assert F.f_j(c, last_m([0] * n), None) is None


def test_k_low60_over_sigma():
    n = 100
    c = bars([100.0] * n, down=1.0)  # low = 99
    m = last_m([0] * n)
    # (100 − 99)/100 = 100 bps; σ = 50 bps → 2
    assert F.f_k(c, m, 100.0, 50.0) == pytest.approx(2.0)
    assert F.f_k(c, m, 100.0, None) is None and F.f_k(c, m, 100.0, 0) is None
    c.d[m - 59 * MIN] = (101.0, 98.0, 100.0, 1.0)  # граница окна 60 — учитывается
    assert F.f_k(c, m, 100.0, 50.0) == pytest.approx(4.0)
    c.d[m - 60 * MIN] = (101.0, 90.0, 100.0, 1.0)  # вне окна
    assert F.f_k(c, m, 100.0, 50.0) == pytest.approx(4.0)


def test_l_turnover24_and_m_cap():
    n = 1600
    c = bars([10.0] * n, vol=3.0)
    m = last_m([0] * n)
    assert F.turnover24(c, m) == pytest.approx(1440 * 10.0 * 3.0)
    c.d.pop(m - 100 * MIN)
    c.k = sorted(c.d)
    assert F.turnover24(c, m) is None  # неполные сутки → признака нет
    short = bars([10.0] * 1000, vol=3.0)
    assert F.turnover24(short, last_m([0] * 1000)) is None
    # Г-123: $500 / оборот, % — как в build_feats
    assert F.POSITION_USD / (1440 * 30.0) * 100 == pytest.approx(500 / 43200 * 100)


def test_o_off_center():
    n = 100
    closes = [100.0] * n
    c = bars(closes, up=10.0, down=10.0)  # диапазон 90…110
    m = last_m(closes)
    assert F.f_o(c, m, 100.0) == pytest.approx(0.0)       # центр
    assert F.f_o(c, m, 110.0) == pytest.approx(0.5)       # у верхней границы
    assert F.f_o(c, m, 90.0) == pytest.approx(0.5)        # у нижней
    flat = bars(closes)
    assert F.f_o(flat, m, 100.0) is None                  # hi == lo
    assert F.f_o(c, m, None) is None


def test_p_wall_traded_in_ticks():
    n = 1600
    c = bars([100.0] * n, up=1.0, down=1.0)  # 99…101 каждую минуту
    m = last_m([0] * n)
    tick = 0.01
    assert F.f_p(c, m, 10000, tick) == 1   # 100.00 внутри диапазона
    assert F.f_p(c, m, 10100, tick) == 1   # ровно на high 101,00
    assert F.f_p(c, m, 10101, tick) == 0   # выше
    assert F.f_p(c, m, 9899, tick) == 0    # ниже low 99,00
    assert F.f_p(c, m, 10000, None) is None
    short = bars([100.0] * 100, up=1.0, down=1.0)
    assert F.f_p(short, last_m([0] * 100), 10000, tick) is None  # окна 24 ч нет


def test_time_feats():
    def at(day_off, hour, minute=0):
        return T0_DAY + day_off * 86_400 * 10 ** 9 + (hour * 3600 + minute * 60) * 10 ** 9
    mon = at(0, 0)  # 03.08.2026 — понедельник
    f = F.time_feats(mon)
    assert f["g14_asia"] == 1 and f["g14_wkend"] == 0 and f["g45_min_to_fund"] == 0
    assert F.time_feats(at(0, 16, 30))["g14_tea"] == 1 and F.time_feats(at(0, 18))["g14_tea"] == 0
    assert F.time_feats(at(0, 17, 59))["g14_tea"] == 1
    assert [F.time_feats(at(0, h))["g14_quiet"] for h in (1, 2, 6, 7, 20, 21, 23)] == [0, 1, 1, 0, 0, 1, 1]
    assert [F.time_feats(at(d, 12))["g14_wkend"] for d in range(7)] == [0, 0, 0, 0, 0, 1, 1]  # пн…вс
    assert F.time_feats(at(0, 7, 15))["g45_min_to_fund"] == 45  # расчёт в 08:00
    assert F.time_feats(at(0, 15, 0))["g45_min_to_fund"] == 60
    assert F.time_feats(at(0, 15, 1))["g45_min_to_fund"] == 59
    assert F.time_feats(at(0, 23, 59))["g45_min_to_fund"] == 1


# ---------- квантили и клетки ----------

def test_rank_cut_matches_p08_quantile():
    vals = [5, 1, 9, 3, 7, 2, 8, 4, 6, 10, 12, 11]  # n = 12
    for num, den in ((1, 3), (2, 3)):
        for low in (True, False):
            assert F.rank_cut(vals, num, den, low) == F.p08c.quantile_cut(vals, num / den, low)
    assert F.rank_cut(vals, 1, 3, True) == 4    # ранг 4 по возрастанию
    assert F.rank_cut(vals, 1, 3, False) == 9   # ранг 4 по убыванию
    assert F.rank_cut([1.0], 2, 3, True) == 1.0


def test_cell_directions_and_none_stays():
    vals = list(range(1, 13))  # 1..12
    t = F.cell_threshold("g11-q33", vals)       # low: оставляем ≤ 4
    assert (t, F.cell_op("g11-q33")) == (4, "<=")
    assert [F.keep_value("<=", t, v) for v in (1, 4, 5)] == [True, True, False]
    t = F.cell_threshold("g62-q33", vals)       # high: ≥ 9
    assert (t, F.cell_op("g62-q33")) == (9, ">=")
    assert F.keep_value(">=", t, 9) and not F.keep_value(">=", t, 8)
    assert F.cell_threshold("g11-q67", vals) == 8
    # N: пропускаем верхнюю треть / две трети → оставляем ≤ ранг 2/3 / 1/3 по возрастанию
    assert F.cell_threshold("g48-q33", vals) == 8 and F.cell_threshold("g48-q67", vals) == 4
    assert F.cell_op("g48-q33") == "<="
    assert F.cell_threshold("g123-cap1", vals) == 1.0 and F.cell_op("g123-cap1") == "<="
    assert F.cell_threshold("g45-fund", vals) == 60.0 and F.cell_op("g45-fund") == ">"
    assert F.keep_value(">", 60.0, 60.0) is False and F.keep_value(">", 60.0, 60.1) is True
    assert F.cell_op("g14-tea") == "==0" and F.keep_value("==0", None, 1) is False and F.keep_value("==0", None, 0)
    assert F.cell_op("g47-hist") == "==1" and F.keep_value("==1", None, 1) and not F.keep_value("==1", None, 0)
    for op in ("<=", ">=", ">", "==0", "==1"):
        assert F.keep_value(op, 1.0, None) is True  # нет значения — сигнал остаётся


def test_cell_count_matches_protocol():
    assert len(F.CELLS) == 23  # 22 клетки П (H–S) + Г-89
    fams = [v[0] for v in F.CELLS.values()]
    assert len(set(fams)) == 16 and sorted(set(fams)) == sorted(COV.FAM_ORDER)


# ---------- семья N ----------

def write_day(d, rows):
    """rows: [(symbol, idx, t0_ns, idle_ns, exit_ns|None, net_bps|None, reason)] → signals.csv + rounds.csv суток."""
    os.makedirs(d, exist_ok=True)
    form = "f"
    day = "2026-08-03"
    with open(os.path.join(d, "signals.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write("# lob bounce-grid: busy_skip=off\n")
        fh.write("symbol,day_utc,form,signal_index,t0_ns,price_tick,entry_px,step,idle_ns,residual,exit_ns\n")
        for sym, idx, t0, idle, ex, _, _ in rows:
            fh.write(f"{sym},{day},{form},{idx},{t0},100,1.0,{'filled' if ex else 'timed_out'},{idle},,{ex or ''}\n")
    with open(os.path.join(d, "rounds.csv"), "w", encoding="utf-8", newline="") as fh:
        fh.write("# lob bounce-grid: busy_skip=off\n")
        fh.write("symbol,day_utc,form,signal_index,t0_ns,dir,entry_px,exit_px,qty,net_bps,reason,exit_ns\n")
        for sym, idx, t0, idle, ex, net, reason in rows:
            if ex:
                fh.write(f"{sym},{day},{form},{idx},{t0},1,1.0,1.0,1,{net},{reason},{ex}\n")


H = 3600 * 10 ** 9


def test_stop_share_strictly_before_t0_and_min_trades(tmp_path):
    t = T0_DAY
    d = str(tmp_path / "d1")
    # AAA: 4 закрытия; закрытия по 1 ч, сигналы разнесены → все приняты
    rows = [("AAAUSDT", i, t + i * 3 * H, t + i * 3 * H + H, t + i * 3 * H + H, -10 if i in (0, 2) else 5,
             "stop" if i in (0, 2) else "take") for i in range(4)]
    rows.append(("TRXUSDT", 9, t, t + H, t + H, -1, "stop"))  # TRX вне пула
    write_day(os.path.join(d, F.SET_), rows)
    cl = F.b1_accepted_closes([os.path.join(d, F.SET_)])
    assert set(cl) == {"AAAUSDT"} and cl["AAAUSDT"][1] == [True, False, True, False]
    ex = cl["AAAUSDT"][0]
    t_after_all = ex[-1] + 1
    assert F.stop_share(cl, "AAAUSDT", t_after_all) == pytest.approx(0.5)
    assert F.stop_share(cl, "AAAUSDT", ex[-1]) == pytest.approx(2 / 3)       # закрытие ровно в t0 — не считается (строго до)
    assert F.stop_share(cl, "AAAUSDT", ex[2]) is None                          # прежних сделок 2 < 3 → признака нет
    assert F.stop_share(cl, "AAAUSDT", ex[2] + 1) == pytest.approx(2 / 3)
    assert F.stop_share(cl, "BBBUSDT", t_after_all) is None
    # окно 14 суток [t0 − 14 сут, t0): закрытие ровно на левой границе входит, на 1 нс раньше — выпадает
    d14 = 14 * 86_400 * 10 ** 9
    assert F.stop_share(cl, "AAAUSDT", ex[0] + d14) == pytest.approx(0.5)      # 4 закрытия T,F,T,F
    assert F.stop_share(cl, "AAAUSDT", ex[0] + d14 + 1) == pytest.approx(1 / 3)  # осталось F,T,F
    assert F.stop_share(cl, "AAAUSDT", ex[1] + d14 + 1) is None                  # осталось 2 < 3


def test_closes_only_accepted_by_busy_automaton(tmp_path):
    t = T0_DAY
    d = str(tmp_path / "d")
    # сигнал 1 раньше окончания занятости сигнала 0 → не принят автоматом; закрытие в rounds есть, но в N не идёт
    rows = [("AAAUSDT", 0, t, t + 2 * H, t + 2 * H, -10, "stop"),
            ("AAAUSDT", 1, t + H, t + 3 * H, t + 3 * H, 10, "take"),
            ("AAAUSDT", 2, t + 5 * H, t + 6 * H, t + 6 * H, -10, "stop")]
    write_day(os.path.join(d, F.SET_), rows)
    cl = F.b1_accepted_closes([os.path.join(d, F.SET_)])
    assert cl["AAAUSDT"][0] == [t + 2 * H, t + 6 * H] and cl["AAAUSDT"][1] == [True, True]


# ---------- блок Г-120 ----------

def s_run(tmp_path, days, **kw):
    dirs = []
    for i, rows in enumerate(days):
        d = os.path.join(str(tmp_path), f"day{i}", F.SET_)
        write_day(d, rows)
        dirs.append(d)
    return F.s_block_keep(dirs, **kw)


def trade(sym, idx, start_h, loss, base=T0_DAY, dur_h=1):
    t0 = base + int(start_h * H)
    ex = t0 + dur_h * H
    return (sym, idx, t0, ex, ex, -10 if loss else 10, "stop" if loss else "take")


def kept_idx(keep, rows):
    ks = {k[:3] for k in keep}
    return [r[1] for r in rows if (r[0], str(r[2]), "100") in ks]


def test_g120_blocks_after_five_losses_for_24h(tmp_path):
    # 5 убытков: сделка i входит в 2i ч и выходит в 2i+1 ч; 5-я (idx 4) выходит в 9 ч → блок (9 ч, 33 ч]
    rows = [trade("AAAUSDT", i, 2 * i, True) for i in range(8)]
    rows.append(trade("AAAUSDT", 8, 33, True))      # ровно на конце блока (≤) → заблокирован
    rows.append(trade("AAAUSDT", 9, 33.5, True))    # после блока → принят
    keep, cnt = s_run(tmp_path, [rows])
    assert kept_idx(keep, rows) == [0, 1, 2, 3, 4, 9]
    assert cnt["blocked"] == 4 and cnt["block_events"] == 1
    # тот же ряд без пятого убытка подряд (победа в середине) — блока нет
    rows2 = [trade("AAAUSDT", i, 2 * i, i != 2) for i in range(8)]
    keep, cnt = s_run(tmp_path / "x", [rows2])
    assert kept_idx(keep, rows2) == list(range(8)) and cnt["blocked"] == 0


def test_g120_win_resets_streak_and_other_coin_independent(tmp_path):
    a = [trade("AAAUSDT", i, 2 * i, loss) for i, loss in enumerate([1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1])]
    b = [trade("BBBUSDT", i, 2 * i, True) for i in range(5)] + [trade("BBBUSDT", 5, 2 * 5, False)]
    keep, cnt = s_run(tmp_path, [a + b])
    ka = kept_idx(keep, a)
    # AAA: 4 убытка, прибыль → сброс; затем убытки idx 5..9 (5 подряд) → блок после idx 9; idx 10 заблокирован
    assert ka == list(range(10)) and cnt["blocked"] == 2  # AAA: блокирован idx 10; BBB: idx 5 (прибыльный, но внутри блока)
    kb = kept_idx(keep, b)
    assert kb == [0, 1, 2, 3, 4]  # BBB: 5 подряд → шестой (прибыльный) сигнал в блоке отброшен


def test_g120_blocked_signal_does_not_occupy_coin(tmp_path):
    # убытки 0..4, сигнал 5 внутри блока (отброшен), сигнал 6 после блока принят даже если сигнал 5 «шёл бы» до него
    rows = [trade("AAAUSDT", i, 2 * i, True) for i in range(5)]
    rows.append(("AAAUSDT", 5, T0_DAY + 11 * H, T0_DAY + 60 * H, T0_DAY + 60 * H, -10, "stop"))  # шёл бы 49 ч — монету не занимает
    rows.append(trade("AAAUSDT", 6, 36, False))  # t0 = 36 ч > 34 ч (конец блока) — принят
    keep, cnt = s_run(tmp_path, [rows])
    assert kept_idx(keep, rows) == [0, 1, 2, 3, 4, 6] and cnt["blocked"] == 1


def test_g120_state_carries_across_days(tmp_path):
    day1 = [trade("AAAUSDT", i, 2 * i, True) for i in range(3)]
    day2 = [trade("AAAUSDT", 3 + i, 8 + 2 * i, True, base=T0_DAY + 0) for i in range(2)]  # t0 = 8, 10 ч — «сутки 2»
    day2 += [trade("AAAUSDT", 5, 14, False)]
    keep, cnt = s_run(tmp_path, [day1, day2])
    assert cnt["blocked"] == 1 and kept_idx(keep, day2) == [3, 4]


def test_g120_gate_no_block_keeps_everything_like_plain_busy_replay(tmp_path):
    rows = [trade("AAAUSDT", i, 2 * i, loss) for i, loss in enumerate([1, 1, 0, 1, 1, 0, 1, 1, 1, 1, 0])]
    rows += [("BBBUSDT", 20, T0_DAY, T0_DAY + 5 * H, T0_DAY + 5 * H, -10, "stop"),
             ("BBBUSDT", 21, T0_DAY + H, T0_DAY + 6 * H, T0_DAY + 6 * H, 10, "take")]  # 21 — занят автоматом
    d = os.path.join(str(tmp_path), "d", F.SET_)
    write_day(d, rows)
    keep, cnt = F.s_block_keep([d])
    assert cnt["blocked"] == 0 and cnt["block_events"] == 0
    # ворота: фильтр «все сигналы» через keep-файл даёт тот же набор принятых, что busy-replay без фильтра
    sp = os.path.join(d, "signals.csv")
    plain, _ = F.br.replay(sp, None)
    kp = tmp_path / "keep.csv"
    F.p07.write_keep(sorted({k[:3] for k in keep}), str(kp))
    with_keep, _ = F.br.replay(sp, F.br.load_keep(str(kp)))
    assert plain == with_keep and len(plain) == 12


# ---------- Г-89: номинал стены и порог ----------

def test_wall_usd_and_arm_px_from_d20(tmp_path):
    root = tmp_path / "study" / "root-2026-08-03"
    d20 = tmp_path / "d20" / "2026-08-03"
    os.makedirs(root)
    os.makedirs(d20)
    (root / "instruments.csv").write_text("symbol,tick_size,min_order_qty,qty_step\nAAAUSDT,0.01,0.1,0.1\n", encoding="utf-8")
    (d20 / "approaches-AAAUSDT.csv").write_text(
        "day_utc,side,price_tick,approach_index,arm_ms,age_ms,arm_dist_bps,birth_ms,size_at_arm,best_own_tick\n"
        "2026-08-03,bid,10000,0,1000,1,2,0,50000,10002\n2026-08-03,ask,10100,0,1000,1,2,0,9,10099\n", encoding="utf-8")
    ref = F.Ref(sigma_dir=str(tmp_path), study_roots=[str(tmp_path / "study")], d20_roots=[str(tmp_path / "d20")])
    # номинал = price_tick × tick_size × size_at_arm × qty_step = 10000 × 0.01 × 50000 × 0.1 = 500 000
    assert ref.wall_usd("2026-08-03", "AAAUSDT", 1000 * 10 ** 6, 10000) == pytest.approx(500_000.0)
    assert ref.arm_px("2026-08-03", "AAAUSDT", 1000 * 10 ** 6, 10000) == pytest.approx(100.02)
    assert ref.wall_usd("2026-08-03", "AAAUSDT", 2000 * 10 ** 6, 10000) is None  # подхода нет в D20 → нет значения
    assert ref.wall_usd("2026-08-03", "ZZZUSDT", 1000 * 10 ** 6, 10000) is None
    vals = [float(v) for v in range(1, 13)]
    thr = F.cell_threshold("p10-g89-mkt-big", vals)  # верхняя треть: ранг 4 по убыванию → 9
    assert thr == 9.0 and F.cell_op("p10-g89-mkt-big") == ">="
    assert F.keep_value(">=", thr, 9.0) and not F.keep_value(">=", thr, 8.99)


# ---------- сборка признаков на синтетике ----------

def test_build_feats_end_to_end_and_signal_minute_not_used(tmp_path):
    n = 1700
    start = 1785715200000 - (n - 200) * MIN  # последний бар — через 200 минут после полуночи
    m_sig = 1785715200000 + 150 * MIN       # минута сигнала
    t0_ms = m_sig + 30_000
    t0_ns = t0_ms * 10 ** 6
    day = "2026-08-03"
    closes = [100.0 + 0.001 * (i % 7) for i in range(n)]

    def write(spike):
        kd = tmp_path / "kl"
        os.makedirs(kd, exist_ok=True)
        for sym, mult in (("AAAUSDT", 1.0), ("BTCUSDT", 2.0)):
            p = (tmp_path / "btc.csv") if sym == "BTCUSDT" else (kd / f"ref-{sym}-1m.csv")
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write("minute_ms,open,high,low,close,volume\n")
                for i, c in enumerate(closes):
                    mm = start + i * MIN
                    c2 = c * mult * (1.5 if spike and mm == m_sig else 1.0)
                    fh.write(f"{mm},{c2},{c2 + 0.5},{c2 - 0.5},{c2},2\n")
    root = tmp_path / "study" / f"root-{day}"
    os.makedirs(root)
    (root / "instruments.csv").write_text("symbol,tick_size,min_order_qty,qty_step\nAAAUSDT,0.01,0.1,0.1\n", encoding="utf-8")
    d20 = tmp_path / "d20" / day
    os.makedirs(d20)
    (d20 / "approaches-AAAUSDT.csv").write_text(
        "day_utc,side,price_tick,approach_index,arm_ms,age_ms,arm_dist_bps,birth_ms,size_at_arm,best_own_tick\n"
        f"{day},bid,10000,0,{t0_ms},1,2,0,50000,10001\n", encoding="utf-8")
    sg = tmp_path / "sig"
    os.makedirs(sg)
    (sg / "sigma-AAAUSDT.csv").write_text(f"window_end_ms,sigma_bps\n{m_sig},50.0\n", encoding="utf-8")  # конец окна = m + 1 мин
    sig = [{"symbol": "AAAUSDT", "day_utc": day, "form": "f", "signal_index": "0", "t0_ns": str(t0_ns),
            "price_tick": "10000", "entry_px": "100.0"}]
    ref = F.Ref(sigma_dir=str(sg), study_roots=[str(tmp_path / "study")], d20_roots=[str(tmp_path / "d20")])
    res = {}
    for spike in (False, True):
        write(spike)
        res[spike] = F.build_feats(sig, [str(tmp_path / "kl")], [str(tmp_path / "btc.csv")], ref, {})[0]
    assert res[False] == res[True]  # минута сигнала в признаки не входит
    f = res[False]
    assert f["g76_low60_sigma"] is not None and f["g74_hi5_bps"] is not None   # D20 + σ найдены
    assert f["g48_stop_share_14d"] is None                                       # закрытий нет
    assert f["g22_turnover24_usd"] == pytest.approx(1440 * 2.0 * 100.0, rel=0.01)
    assert f["g123_cap_pct"] == pytest.approx(500.0 / f["g22_turnover24_usd"] * 100)
    assert f["g14_asia"] == 0 and f["g45_min_to_fund"] == pytest.approx(329.5)  # t0 = 02:30:30 → до 08:00 329,5 мин
    assert f["g14_quiet"] == 1
    assert f["g47_wall_traded"] in (0, 1)
    # σ-ключ: строка с window_end_ms = m + 1 мин даёт K; со смещением на минуту — признака нет
    (sg / "sigma-AAAUSDT.csv").write_text(f"window_end_ms,sigma_bps\n{m_sig - MIN},50.0\n", encoding="utf-8")
    ref2 = F.Ref(sigma_dir=str(sg), study_roots=[str(tmp_path / "study")], d20_roots=[str(tmp_path / "d20")])
    assert F.build_feats(sig, [str(tmp_path / "kl")], [str(tmp_path / "btc.csv")], ref2, {})[0]["g76_low60_sigma"] is None


def test_write_and_read_feats_roundtrip(tmp_path):
    sig = [{"symbol": "AAAUSDT", "day_utc": "d", "form": "f", "signal_index": "0", "t0_ns": "1", "price_tick": "2",
            "entry_px": "3.5"}]
    feats = [dict.fromkeys(F.FEATS)]
    feats[0]["g62_er"] = 0.1 + 0.2
    feats[0]["g47_wall_traded"] = 1
    F.write_feats(str(tmp_path / "x" / "f.csv"), sig, feats, {"g89_usd": [None]})
    rows = F.read_feats(str(tmp_path / "x" / "f.csv"))
    assert rows[0]["g62_er"] == 0.1 + 0.2 and rows[0]["g47_wall_traded"] == 1.0 and rows[0]["coin_abs_minus_btc_1h"] is None
    assert rows[0]["g89_usd"] is None and rows[0]["symbol"] == "AAAUSDT"


# ---------- охват: decide на синтетике ----------

def test_decide_labels_merge_and_json(tmp_path, monkeypatch):
    cov = tmp_path / "cov"
    n_aug, n_sep = 90, 30

    def feats_rows(m, n):
        rows = []
        for i in range(n):
            r = {k: float(i) + 1.0 for k in F.FEATS}  # по умолчанию значения разные у каждого сигнала
            r.update(symbol="AAAUSDT", day_utc="2026-08-03" if m == "aug" else "2026-09-03", form="f", signal_index=str(i),
                     t0_ns=str((1 if m == "aug" else 2) * 10 ** 18 + i), price_tick="1", entry_px="1.0")
            r["coin_abs_minus_btc_1h"] = float(i)
            r["g62_er"] = 0.5          # все равны → q33 и q67 совпадут по порогу
            r["g123_cap_pct"] = 0.5    # cap < 1 у всех → > 90 % сигналов
            r["g14_tea"] = int(i % 10 == 0)
            for k in ("g14_asia", "g14_quiet", "g14_wkend"):
                r[k] = 0
            r["g45_min_to_fund"] = 100.0
            r["g47_wall_traded"] = 1
            r["g89_usd"] = float(i)
            rows.append(r)
        return rows

    for m, n in (("aug", n_aug), ("sep", n_sep)):
        rows = feats_rows(m, n)
        F.write_feats(str(cov / m / "feats-p10.csv"), rows, rows, {"g89_usd": [r["g89_usd"] for r in rows]})
        with open(cov / m / "g89-market.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(F.BASE_COLS + ["g89_usd"])
            for r in rows:
                w.writerow([r[c] for c in F.BASE_COLS] + [r["g89_usd"]])
    monkeypatch.setattr(COV, "COV", str(cov))
    monkeypatch.setattr(F, "s_block_keep",
                        lambda dirs: ([("AAAUSDT", "1", "1", "2026-08-03")] * 80 + [("AAAUSDT", "1", "1", "2026-09-03")] * 30,
                                      {"block_events": 3, "signals": 120, "blocked": 10}))
    monkeypatch.setattr(F, "chrono_day_dirs", lambda: [])
    monkeypatch.setattr(F.p08c, "T", {"aug": 424, "sep": 141})
    COV.cmd_decide(F)
    j = json.load(open(cov / "p10-coverage.json", encoding="utf-8"))
    byname = {c["cell"]: c for c in j["cells"]}
    assert j["signals_b1"] == {"aug": 90, "sep": 30}
    cap = byname["g123-cap1"]
    assert cap["mirror_gt90"] and cap["label"] == COV.LABEL_NONE and cap["f"]["aug"] == 1.0
    assert byname["g62-q33"]["thr"] == 0.5 == byname["g62-q67"]["thr"]
    assert [m["cells"] for m in j["merged"] if m["family"] == "I"] == [["g62-q33", "g62-q67"]]
    assert [c["name"] for c in j["final"]["I"]] == ["g62-q33"]
    assert j["cells_after"] == 23 - len(j["merged"])
    assert j["m_family"] == j["m_family_p"] + 3 and j["m_family_without_merge"] == 19
    assert byname["g14-tea"]["f"]["aug"] == pytest.approx(0.9, abs=0.001)
    assert byname["g120-block5"]["blocked_signals"] == {"aug": 10, "sep": 0}
    # Г-89: порог — ранг ⌈90/3⌉ = 30 по убыванию среди номиналов августа 0..89 → 60; в числовом поле JSON
    assert j["usd_min_g89"] == 60.0 and byname["p10-g89-mkt-big"]["usd_min"] == 60.0
    assert byname["p10-g89-mkt-big"]["f"]["aug"] == pytest.approx(30 / 90, abs=1e-3)
    # квантильные клетки при «> 90 %» / «< 30» получают тот же порог (замена §7 — no-op)
    assert byname["g11-q33"]["thr"] == 29.0 and byname["g11-q67"]["thr"] == 59.0
    # keep-файлы
    COV.cmd_keep(F)
    kp = cov / "keep"
    assert sum(1 for _ in open(kp / "keep-p10-keepall.csv", encoding="utf-8")) == 1 + 120  # шапка + все (уникальные t0)
    assert sum(1 for _ in open(kp / "keep-g11-q33.csv", encoding="utf-8")) > 1
