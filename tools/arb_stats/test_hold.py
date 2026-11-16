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


def test_max_gap_raw_liveness():
    import hold_filter as hf
    e = np.array([1000, 2000, 3000, 9500, 10_000, 40_000], np.int64)
    assert hf.max_gap(e, 2000, 10_000, pad=1000) == 6500            # 3000 → 9500
    assert hf.max_gap(e, 12_000, 20_000, pad=1000) == 10_000        # записей нет: от a − pad до b + pad = 9
    assert hf.max_gap(e, 1500, 2500, pad=500) == 1000
