"""Тесты счёта arb-holding: участки, длительность эпизода, возраст ног, виды «от нуля» / «от центра»."""
import numpy as np

import hold_scan as hs


def book(ev):
    """ev — [(t_мс, bid, ask)] -> книга как после core.clean (e, rx, lag, bid, ask, dbid, dask)."""
    a = np.array(ev, float)
    n = len(a)
    return {"e": a[:, 0].astype(np.int64), "rx": a[:, 0].astype(np.int64), "lag": np.zeros(n, np.int32), "bid": a[:, 1], "ask": a[:, 2],
            "dbid": np.full(n, 500.0), "dask": np.full(n, 400.0)}


def test_runs_and_reduce():
    m = np.array([0, 1, 1, 0, 1, 0, 0, 1, 1, 1], bool)
    st, en = hs.runs_of(m)
    assert st.tolist() == [1, 4, 7] and en.tolist() == [2, 4, 9]
    x = np.array([5, 1, 9, 3, 7, 2, 2, 4, 8, 6], float)
    assert hs.reduce_runs(x, st, en, np.maximum).tolist() == [9, 7, 8]
    assert hs.reduce_runs(x, st, en, np.minimum).tolist() == [1, 7, 4]


def test_zero_view_episode_duration_and_age():
    # B стоит на 100/100.1 всё время (одна запись — нога B «стоит»); A шумит у 100, потом bid_A = 101 на 7 с, потом обратно
    t0 = 1_000_000
    ev_a = [(t0 + 1000 * k, 100.0 + 0.001 * (k % 3), 100.1 + 0.001 * (k % 3)) for k in range(0, 60)]
    ev_a += [(t0 + 60_000, 101.0, 101.1), (t0 + 67_000, 100.0, 100.1), (t0 + 68_000, 100.0, 100.11)]
    A, B = book(ev_a), book([(t0 - 1000, 100.0, 100.1)])
    r = hs.scan_pair(A, B, t0, t0 + 120_000)
    e = r["eps"][(50.0, "z")]
    assert len(e) == 1
    row = dict(zip(hs.COLS, e[0]))
    assert row["t"] == t0 + 60_000 and row["dur"] == 7000 and row["side"] == 1 and row["open"] == 0
    assert row["ageB"] == 68_000 and row["ageA"] == 7000.0             # возраст к концу эпизода: B не менялась с t0 − 1 с; единственная котировка A стояла все 7 с
    assert abs(row["edge0"] - (101.0 / 100.1 - 1) * 1e4) < 1e-6
    assert row["size0"] == 400.0                                      # min(dbid_A = 500, dask_B = 400)
    assert r["nruns"][(50.0, "z")][1] == 1                            # эпизодов ≥ 2 с
    assert len(r["eps"][(100.0, "z")]) == 0


def test_open_episode_flagged():
    t0 = 1_000_000
    ev_a = [(t0 + 1000 * k, 100.0, 100.1) for k in range(60)] + [(t0 + 60_000, 101.0, 101.1)]
    r = hs.scan_pair(book(ev_a), book([(t0 - 1000, 100.0, 100.1)]), t0, t0 + 90_000)
    row = dict(zip(hs.COLS, r["eps"][(50.0, "z")][0]))
    assert row["open"] == 1 and row["dur"] == 30_000


def test_centre_view_needs_centre():
    t0 = 1_000_000
    ev_a = [(t0 + 1000 * k, 100.0, 100.1) for k in range(60)] + [(t0 + 60_000, 101.0, 101.1), (t0 + 70_000, 100.0, 100.1)]
    r = hs.scan_pair(book(ev_a), book([(t0 - 1000, 100.0, 100.1)]), t0, t0 + 120_000)
    assert len(r["eps"][(50.0, "c")]) == 0          # центр появляется только после 30 минутных баров


def test_simulate_enter_after_delay_exit_to_zero():
    t0 = 1_000_000
    ev_a = [(t0 + 1000 * k, 100.0, 100.1) for k in range(60)]
    ev_a += [(t0 + 60_000, 101.0, 101.1), (t0 + 67_000, 99.9, 100.0), (t0 + 90_000, 99.9, 100.0)]
    r = hs.scan_pair(book(ev_a), book([(t0 - 1000, 100.0, 100.1)]), t0, t0 + 120_000)
    sim = dict(zip(hs.SIM_COLS, r["sim"][(50.0, "z")][(1000, "z-t")][0]))
    s1 = (101.0 / 100.1 - 1) * 1e4
    assert sim["tx"] == t0 + 61_000 and abs(sim["edge_tx"] - s1) < 1e-6 and sim["size"] == 400.0
    assert sim["te"] == t0 + 68_000                                  # сигнал выхода в 67 с (M ≤ 0) + 1 с
    assert abs(sim["gross"] - s1) < 1e-6 and abs(sim["net"] - (s1 - hs.FEE_RT_BPS)) < 1e-6
    # задержка 5 с: вход в 65 с, расхождение 101 ещё стоит (7 с) — вход по 101, выход как выше + 5 с
    sim5 = dict(zip(hs.SIM_COLS, r["sim"][(50.0, "z")][(5000, "z-t")][0]))
    assert sim5["tx"] == t0 + 65_000 and sim5["te"] == t0 + 72_000 and abs(sim5["edge_tx"] - s1) < 1e-6


def test_simulate_entry_after_vanish_loses():
    t0 = 1_000_000
    ev_a = [(t0 + 1000 * k, 100.0, 100.1) for k in range(60)] + [(t0 + 60_000, 101.0, 101.1), (t0 + 62_000, 100.0, 100.1)] + [(t0 + 100_000, 100.0, 100.12)]
    r = hs.scan_pair(book(ev_a), book([(t0 - 1000, 100.0, 100.1)]), t0, t0 + 200_000)
    sim = dict(zip(hs.SIM_COLS, r["sim"][(50.0, "z")][(5000, "x1-t")][0]))
    assert sim["tx"] == t0 + 65_000 and sim["edge_tx"] < 0          # к моменту входа (65 с) расхождения нет: Sell < 0
    assert sim["how"] == 2 or np.isnan(sim["net"])                   # противоположного разрыва ≥ θ нет — закрытие по потолку удержания (1 ч) выходит за окно: не закрыт


def test_filter_helpers():
    import hold_filter as hf
    s, e = hf.merge([(100, 200), (150, 300), (500, 600)])
    assert s.tolist() == [100, 500] and e.tolist() == [300, 600]
    a, b = np.array([0, 250, 301, 550, 700]), np.array([50, 260, 400, 560, 800])
    assert hf.overlaps(s, e, a, b).tolist() == [False, True, False, True, False]
    # пн 16.11.2026 15:00 UTC — сессия акций открыта; вс 15.11 15:00 — закрыта; пн 21:00 — закрыта; сырьё: пн закрыто=False, сб True
    from datetime import datetime, timezone
    ms = lambda s_: int(datetime.fromisoformat(s_).replace(tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
    assert not hf.session_closed("equity", np.array([ms("2026-11-16 15:00")]))[0]
    assert hf.session_closed("equity", np.array([ms("2026-11-15 15:00")]))[0]
    assert hf.session_closed("etf", np.array([ms("2026-11-16 21:00")]))[0]
    assert not hf.session_closed("commodity", np.array([ms("2026-11-16 03:00")]))[0]
    assert hf.session_closed("forex", np.array([ms("2026-11-14 12:00")]))[0]


def test_max_free_gap_overlap_rule():
    import hold_filter as hf
    gp, gn = np.array([3000, 10_000, 100_000], np.int64), np.array([9500, 16_000, 140_000], np.int64)     # интервалы молчания символа > 5 с
    a, b = np.array([2000, 9600, 16_000, 20_000, 120_000]), np.array([10_000, 9900, 17_000, 30_000, 121_000])
    # эпизод [2000;10000] пересекает (3000;9500) 6500 и (10000;16000)? — p=10000 не < b=10000, нет; [9600;9900] внутри (3000;9500)? n=9500 < a — нет, пересечений нет;
    # [16000;17000] — n=16000 не > a, нет; [20000;30000] — между интервалами, нет; [120000;121000] внутри (100000;140000) — 40000
    assert hf.max_free_gap(gp, gn, a, b).tolist() == [6500, 0, 0, 0, 40_000]
    assert hf.max_free_gap(np.array([], np.int64), np.array([], np.int64), a, b).tolist() == [0] * 5


def test_rawgaps_intersection_of_files():
    import hold_rawgaps as rg
    f1 = (0, 12_000, np.array([0], np.int64), np.array([10_000], np.int64))               # записи 0, 10 с, 12 с
    f2 = (5_000, 30_000, np.array([5_000], np.int64), np.array([30_000], np.int64))       # записи 5 с, 30 с
    lo, hi = rg.free_intervals([f1, f2])
    assert lo.tolist() == [12_000] and hi.tolist() == [30_000]                            # общие точки 0, 5, 10, 12, 30 с: молчание > 5 с только 12–30
    f3 = (0, 40_000, np.array([0], np.int64), np.array([40_000], np.int64))
    lo, hi = rg.free_intervals([f3])
    assert lo.tolist() == [0] and hi.tolist() == [40_000]
    lo, hi = rg.free_intervals([f1])
    assert lo.tolist() == [0] and hi.tolist() == [10_000]


def test_rolling_centre_window_params():
    import core
    mins = np.arange(100, 160)
    sb = np.zeros((4, 60))
    bb = np.zeros((4, 60))
    bb[3, 30:] = 100.0 * 2                                       # середина закрытия: 0 до 30-й минуты, потом 100 bps (сдвиг уровня)
    m0, c30 = core.rolling_centre(mins, sb, bb, 30, 15)
    m0, c60 = core.rolling_centre(mins, sb, bb)
    assert m0 == 100 and c30[59] == 100.0 and c60[59] == 0.0      # к 59-й минуте медиана 30-минутного окна — новый уровень, 60-минутного — старый
    assert np.isnan(c30[10]) and not np.isnan(c30[16])           # нужно ≥ 15 баров


def test_tolerant_bridges_short_dips_only():
    # B стоит на 100/100.1; A: 3 с на 101, провал 0,4 с, 3 с на 101, провал 1,2 с, 2 с на 101
    t0 = 1_000_000
    ev_a = [(t0 + 1000 * k, 100.0, 100.1) for k in range(60)]
    ev_a += [(t0 + 60_000, 101.0, 101.1), (t0 + 63_000, 100.0, 100.1), (t0 + 63_400, 101.0, 101.1), (t0 + 66_400, 100.0, 100.1),
             (t0 + 67_600, 101.0, 101.1), (t0 + 69_600, 100.0, 100.1), (t0 + 90_000, 100.0, 100.12)]
    A, B = book(ev_a), book([(t0 - 1000, 100.0, 100.1)])
    strict = hs.scan_pair(A, B, t0, t0 + 120_000, tol=0, sim=False)["eps"][(50.0, "z")]
    assert [row[hs.CI["dur"]] for row in strict] == [3000.0, 3000.0, 2000.0]
    tol = hs.scan_pair(A, B, t0, t0 + 120_000, tol=1000, sim=False)["eps"][(50.0, "z")]
    assert [row[hs.CI["dur"]] for row in tol] == [6400.0, 2000.0]                  # провал 0,4 с склеен, провал 1,2 с — нет
    assert tol[0][hs.CI["ndip"]] == 1 and tol[0][hs.CI["tdip"]] == 400.0 and tol[1][hs.CI["ndip"]] == 0
    assert strict[0][hs.CI["ndip"]] == 0


def test_funding_over_hold_signs_and_bounds():
    import pandas as pd
    import hold_filter as hf
    df = pd.DataFrame({"exA": ["bybit", "bybit"], "symA": ["XUSDT", "XUSDT"], "exB": ["okx", "okx"], "symB": ["X-USDT-SWAP", "X-USDT-SWAP"], "side": [1, -1]})
    one = lambda pay: {"b": np.array([1]), "p": np.array([1.0]), "pay": pay}  # noqa: E731
    marks = {"bybit": {"XUSDT": one([[1000, 0.0001], [9000, 0.0002]])}, "okx": {"X-USDT-SWAP": one([[1000, 0.00005]])}}
    f, n = hf.funding_over_hold(df, marks, np.array([500.0, 500.0]), np.array([2000.0, 2000.0]))
    assert n.tolist() == [2, 2] and abs(f[0] - (0.0001 - 0.00005) * 1e4) < 1e-9 and abs(f[1] + f[0]) < 1e-9     # продаём A, покупаем B: получаем ставку A, платим ставку B; обратная сторона — знак минус
    f, n = hf.funding_over_hold(df.iloc[:1], marks, np.array([1000.0]), np.array([9000.0]))                     # окно (tx, te]: 1000 не входит, 9000 входит
    assert n.tolist() == [1] and abs(f[0] - 0.0002 * 1e4) < 1e-9
    f, n = hf.funding_over_hold(df.iloc[:1], {}, np.array([0.0]), np.array([1.0]))
    assert np.isnan(f[0]) and np.isnan(n[0])                                                                      # выплаты символов не записаны — неизвестно


def test_trade_row_live_gap_at_entry():
    import pandas as pd
    import hold_stats as hs_
    c = hs_.sim_cols(1, "c-t")
    sub = pd.DataFrame({"side": [1, -1, 1, -1], "day": [0, 0, 0, 0], c["net"]: [1.0, 2.0, -3.0, -4.0], c["gross"]: [13.6, 14.6, 9.6, 8.6], c["size"]: [100.0] * 4, c["tx"]: [1.0] * 4,
                        c["edge_tx"]: [60.0, -70.0, 40.0, -40.0], c["c_tx"]: [0.0] * 4, c["te"]: [2.0] * 4, c["how"]: [1.0] * 4, c["pl"]: [-20.0] * 4, c["plsize"]: [100.0] * 4})
    r = hs_.trade_row(sub, 1, "c-t", 1.0, 1, 50.0, "z")
    assert r["разрыв ≥ θ на входе, %"] == 50.0 and r["net при живом разрыве, медиана bps"] == 1.5      # сторона +1: Sell = 60 ≥ 50; сторона −1: −Buy = 70 ≥ 50; остальные два — разрыв 40 < 50


def test_simulate_centre_exit_in_zero_view_needs_deviation():
    """«От нуля» + выход «к центру»: если в момент сигнала отклонения от центра нет (разрыв — обычный уровень пары), круг не открывается; при отклонении ≥ 50 bps — открывается."""
    t0 = 1_000_000
    # центр 120 bps (уровень пары), на сигнале разрыв 190 bps — отклонение 70 bps
    T = np.arange(t0, t0 + 400_000, 1000, dtype=np.int64)
    n = len(T)
    S = np.full(n, 120.0)
    S[100:200] = 190.0
    Bu = S - 20.0
    M = (S + Bu) / 2
    cT = np.full(n, 120.0)
    A = book([(int(t), 100.0, 100.1) for t in T])
    B = book([(int(t), 100.0, 100.1) for t in T])
    eps = np.zeros((1, len(hs.COLS)))
    eps[0, hs.CI["t"]], eps[0, hs.CI["side"]], eps[0, hs.CI["i0"]] = T[100], 1, 100
    r = hs.simulate(A, B, T, S, Bu, M, cT, t0 + 400_000, 50.0, "z", eps, 1000, "c-t", {}, t_lo=t0, seed=1)
    assert np.isfinite(r[0, hs.SIM_COLS.index("tx")])               # отклонение 70 bps ≥ 50 — вход есть
    S2 = S.copy()
    S2[100:200] = 150.0                                         # отклонение 30 bps < 50 — вход запрещён
    r2 = hs.simulate(A, B, T, S2, S2 - 20.0, (S2 + S2 - 20.0) / 2, cT, t0 + 400_000, 50.0, "z", eps, 1000, "c-t", {}, t_lo=t0, seed=1)
    assert np.isnan(r2[0, hs.SIM_COLS.index("tx")])


def test_placebo_pays_spread_and_fee():
    """Случайный вход в ровной книге без движения цены: gross = −(спред A + спред B) в долях, net = gross − комиссия."""
    t0 = 1_000_000
    ev = [(t0 + 1000 * k, 100.0, 100.1) for k in range(500)]
    A, B = book(ev), book(ev)
    pl, ps = hs.placebo(A, B, t0, t0 + 500_000, np.array([t0 + 10_000]), np.array([t0 + 40_000]), np.array([1]), np.random.default_rng(3))
    sp = (100.1 / 100.0 - 1) * 1e4
    exp = (100.0 / 100.1 - 1) * 1e4 - (100.1 / 100.0 - 1) * 1e4 - hs.FEE_RT_BPS         # продать A по биду / купить B по оферу, обратно по оферу A / биду B
    assert abs(pl[0] - exp) < 1e-6 and ps[0] == 400.0 and sp > 0


def test_stay_mark_before_uses_strictly_earlier_bucket():
    import hold_stay as hy
    mk = {"binance": {"X": {"b": np.array([100, 101, 105], np.int64), "p": np.array([10.0, 11.0, 15.0]), "pay": []}}}
    ts = np.array([1_010_000, 1_019_999, 1_020_000, 1_050_000, 1_060_000, 1_000_000], np.int64)       # ведра 101, 101, 102, 105, 106, 100
    got = hy.mark_before(mk, "binance", "X", 2.0, ts)
    assert got[:5].tolist() == [5.0, 5.0, 5.5, 5.5, 7.5]              # ведро начала в счёт не идёт: 101 → запись ведра 100, 102 → 101, 105 → 101, 106 → 105
    assert np.isnan(got[5])                                           # раньше первой записи — нет
    assert np.isnan(hy.mark_before(mk, "binance", "X", 1.0, np.array([1_960_000], np.int64)))[0]      # ведро 196: последняя запись 105 старше 90 ведёр
    assert np.isnan(hy.mark_before(mk, "bybit", "X", 1.0, ts)).all()


def test_stay_silence_intervals():
    import hold_stay as hy
    s, e = hy.union_intervals([(np.array([100, 500], np.int64), np.array([300, 600], np.int64)), (np.array([250, 900], np.int64), np.array([400, 1000], np.int64))])
    assert s.tolist() == [100, 500, 900] and e.tolist() == [400, 600, 1000]
    assert hy.silence_time(s, e, np.array([50, 100, 250, 450, 550, 2000])).tolist() == [0, 0, 150, 300, 350, 500]
    assert hy.ticks_outside(s, e, np.array([100, 101, 399, 400, 450, 550, 900, 950, 1000], np.int64)).tolist() == [True, False, False, True, True, False, True, False, True]
    es, ee = hy.union_intervals([])
    assert not len(es) and hy.ticks_outside(es, ee, np.array([5], np.int64)).tolist() == [True] and hy.silence_time(es, ee, np.array([5])).tolist() == [0]


def test_stay_label_windows():
    import hold_stay as hy
    T = np.arange(0, 41, dtype=np.int64) * 60_000
    M = np.full(41, 60.0)
    M[3] = -5.0                                    # возврат на 3-й минуте (валидный тик)
    M[20] = -5.0                                   # возврат на 20-й минуте, но тик внутри молчания сырья
    valid = np.ones(41, bool)
    valid[20] = False
    t = np.array([0, 600_000, 0, 0, 0, 0, 700_000], np.int64)
    st = np.array([0, 10, 0, 0, 0, 0, 11])
    side = np.ones(7, int)
    c0 = np.array([0.0, 0.0, 0.0, np.nan, 0.0, 0.0, 0.0])
    hole = np.array([False, False, True, False, False, False, False])
    cover = np.array([1.0, 1.0, 1.0, 1.0, 0.8, 1.0, 1.0])
    raw_unknown = np.array([False, False, False, False, False, True, False])
    tau, why, label = hy.label_units(T, M, valid, cover, t, st, side, c0, 41 * 60_000, hole, raw_unknown)
    assert tau[0] == 180_000 and label[0] == 1                         # возврат на валидном тике
    assert np.isnan(tau[1]) and label[1] == 0 and why[1] == ""         # тик возврата внутри молчания сырья не считается: «остался»
    assert label[2] == -1 and why[2] == "hole"                         # окно метки пересекает дыру
    assert label[3] == -1 and why[3] == "c0"                           # центр NaN — не 0
    assert label[4] == -1 and why[4] == "cover"                        # вне молчания 80 % < 95 %
    assert label[5] == -1 and why[5] == "raw"                          # у ноги нет сырья
    assert label[6] == -1 and why[6] == "tail"                         # 11:40 + 30 мин > конца окна


def test_stay_quiet_price_is_valid_while_raw_flows():
    """Лучшая цена ноги стоит 10 мин, но записи (смены размера) идут — молчания нет: окно валидно целиком. Молчание сырья 3 мин в окне 30 мин — 90 % < 95 %."""
    import hold_stay as hy
    s, e = hy.union_intervals([(np.array([], np.int64), np.array([], np.int64))])
    cover = 1.0 - (hy.silence_time(s, e, np.array([1_800_000])) - hy.silence_time(s, e, np.array([0]))) / hy.HORIZON_MS
    assert cover[0] == 1.0
    s, e = hy.union_intervals([(np.array([600_000], np.int64), np.array([780_000], np.int64))])
    cover = 1.0 - (hy.silence_time(s, e, np.array([1_800_000])) - hy.silence_time(s, e, np.array([0]))) / hy.HORIZON_MS
    assert abs(cover[0] - 0.9) < 1e-12 and cover[0] < hy.COVER_MIN


def test_stay_design_indicators_from_fit_only():
    import hold_stay as hy
    import pandas as pd
    rng = np.random.default_rng(2)
    tr = pd.DataFrame({f: rng.normal(size=50) for f in hy.FEATURES})
    te = pd.DataFrame({f: rng.normal(size=20) for f in hy.FEATURES})
    te.loc[0, "depth"] = np.nan
    a, b = hy.design(tr, te)
    assert a.shape[1] == b.shape[1] == len(hy.FEATURES)               # пропуск только в проверке — индикатора нет
    tr.loc[3, "depth"] = np.nan
    a, b = hy.design(tr, te)
    assert a.shape[1] == b.shape[1] == len(hy.FEATURES) + 1           # пропуск в подборе — индикатор в обоих


def test_stay_missing_feature_means_converges():
    import hold_stay as hy
    y = np.array([1, 1, 1, 1, 0, 0, 0, 0, 0, 0])
    x = np.array([5, 6, 7, np.nan, 1, 2, 3, np.nan, np.nan, 4.0])
    rule = hy.fit_rule(y, x)
    pred = hy.apply_rule(rule, x)
    assert not pred[3] and not pred[7] and not pred[8]                 # пропуск = «сходится»
    assert abs(rule[2] - hy.bal_acc(y, pred)[0]) < 1e-12               # BA подбора — по всем единицам, включая пропуски
    assert rule[2] < 1.0                                               # единица с пропуском среди «остался» не найдена


def test_stay_split_days_and_embargo():
    import hold_stay as hy
    from hold_common import day_edges
    ms = lambda s_: int(pd_ts(s_))  # noqa: E731
    t0, t1 = ms("2026-11-12 11:37"), ms("2026-11-17 00:03")
    days = day_edges(t0, t1)
    ok, early, late = hy.split_days(days)
    assert ok == [1, 2, 3, 4] and early == [1, 2] and late == [3, 4]  # 12.11 (≈ 8 ч) и 17.11 (≈ 4 ч) не берутся
    days5 = day_edges(t0, ms("2026-11-17 12:00"))                      # 17.11 — 16 ч покрытия: пять полных суток
    ok, early, late = hy.split_days(days5)
    assert ok == [1, 2, 3, 4, 5] and early == [1, 2] and late == [4, 5]   # среднее (3) — буфер
    t = np.array([100, 500, 1000, 1500, 2500])
    assert hy.dist_to_block(t, 1000, 2000).tolist() == [900, 500, 0, 0, 500]


def pd_ts(s_):
    from datetime import datetime, timezone
    return datetime.fromisoformat(s_).replace(tzinfo=timezone.utc).timestamp() * 1000


def test_stay_bootstrap_by_coin_and_permutation():
    import hold_stay as hy
    rng = np.random.default_rng(1)
    n = 400
    cid = rng.integers(0, 20, n)
    y = (rng.random(n) < 0.4).astype(int)
    good = y == 1
    cnt = hy.coin_counts(y, good, cid, 20)
    assert cnt.sum() == n and hy.ba_of(cnt.sum(0)) == 1.0
    W = hy.boot_weights(20, n_boot=500, chunk=200)
    assert W.shape == (500, 20) and (W.sum(1) == 20).all()
    assert hy.boot_ba(cnt, W).min() == 1.0                              # точное правило — BA = 1 в каждом повторе
    rnd = rng.random(n) < 0.5
    ci = hy.ci(hy.boot_ba(hy.coin_counts(y, rnd, cid, 20), W), 0.05)
    assert ci[0] < 0.5 < ci[1]                                          # случайное правило — интервал накрывает 0,5
    P, N = int(y.sum()), int((y == 0).sum())
    assert hy.perm_p(P, N, int(good.sum()), P) < 0.001 and hy.perm_p(P, N, 100, 0) > 0.99


def test_stay_pair_rate_leave_one_out():
    import hold_stay as hy
    import pandas as pd
    tr = pd.DataFrame({"coin": ["A"] * 4 + ["B"] * 2, "exA": "x", "exB": "y", "y": [1, 1, 0, 0, 1, 1]})
    te = pd.DataFrame({"coin": ["A", "B", "C"], "exA": "x", "exB": "y"})
    x_tr, x_te = hy.pair_rate(tr, te)
    assert np.allclose(x_tr[:4], [1 / 3, 1 / 3, 2 / 3, 2 / 3], equal_nan=True)     # без самой единицы: у «остался» (2 − 1) / 3, у «сходится» 2 / 3
    assert np.isnan(x_tr[4]) and np.isnan(x_tr[5])                      # в паре B после исключения одной — 1 единица < 3
    assert x_te[0] == 0.5 and np.isnan(x_te[1]) and np.isnan(x_te[2])


def test_stay_trades_before_needs_coverage():
    import hold_stay as hy
    b = np.arange(1000, 1100, dtype=np.int64)                          # окна 10 с: 10 000 … 10 990 с мс
    tr = {("gate", "A_USDT"): (b, np.full(100, 2, np.int32), np.zeros(100, np.int32))}
    ts = 1_050 * 10_000
    assert hy.trades_before(tr, "gate", "A_USDT", ts) == 12.0           # 6 окон × 2 сделки
    assert np.isnan(hy.trades_before(tr, "gate", "A_USDT", 1_100 * 10_000 + 700_000))    # запись оборвалась > 10 мин назад — нет данных
    assert np.isnan(hy.trades_before(tr, "gate", "A_USDT", 1_000 * 10_000 - 700_000))    # запись ещё не началась
    assert np.isnan(hy.trades_before(tr, "gate", "B_USDT", ts))          # символа в записи нет
