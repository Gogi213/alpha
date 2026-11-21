"""Тесты §13.68 / §13.68.1 (TK-668): повтор E8 по ленте (порядок стоп / тейк, e ≤ S, задержка 4 мс, тейк строго выше, время, конец данных, деньги), признаки R / X24 / X7 / XP / XV,
шок BTC, причинность (мусор в минутках ≥ t), 54 клетки, маска исключения (NaN проходит), допуск, квантили, дамп уровней, чтение ленты. Синтетика, без движка, без P&L.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk668.py` — выполняет все test_* и печатает итог.
"""
import datetime
import os
import sys
import tempfile
import traceback
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk604_stats as st  # noqa: E402
import tk668_cells as cl  # noqa: E402
import tk668_feat as ft  # noqa: E402

NAN = float("nan")
SLIP, TAKER, MAKER = ft.SLIP, ft.TAKER, ft.MAKER


def eq(a, b):
    return np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float), equal_nan=True)


# ---- E8: исполнение по ленте ---------------------------------------------------------------------------------------------------------

def run8(ts, ps, T=1000, lp=100.0, natr=0.01, kind=0, par=0.002, tk=1.0, time_ms=10 ** 9):
    return ft.sim_e8(np.array(ts, dtype=np.int64), np.array(ps, dtype=np.float64), T, lp, natr, kind, par, tk, time_ms)


E = 100.0 * (1.0 + SLIP)                                   # цена входа при последней цене 100
TK = E * (1.0 + 1.0 * 0.01)                                # тейк при natr 0,01, tk_mult 1


def test_entry_first_trade_at_or_after_t():
    r = run8([900, 1000, 1010], [50.0, 100.0, 100.0])
    assert r[0] == ft.CODE_OK and r[1] == 1000 and np.isclose(r[3], E)                       # сделка до T не берётся, вход p·(1 + 1 бп)
    r = run8([900, 1001, 1010], [50.0, 100.0, 100.0])
    assert r[1] == 1001                                                                      # первая сделка со временем ≥ T


def test_take_is_strictly_above_at_take_price():
    r = run8([1000, 1010, 1020, 1030], [100.0, 100.5, TK, TK + 0.01])
    assert r[0] == 0 and r[2] == 1030 and r[4] == TK and r[5] == ft.K_TAKE and r[6] == 0     # цена ровно Tk — не исполнение; строго выше — по цене Tk
    r = run8([1000, 1010], [100.0, TK])
    assert r[5] == ft.K_EOD                                                                  # равная цена тейк не берёт


def test_stop_trigger_then_exec_after_4ms():
    r = run8([1000, 1010, 1012, 1013, 1014], [100.0, 99.7, 99.6, 99.55, 99.5])               # S = 99.8; триггер 1010; исполнение — первая сделка ≥ 1014
    assert r[0] == 0 and r[2] == 1014 and r[5] == ft.K_STOP and np.isclose(r[4], 99.5 * (1.0 - SLIP))
    r = run8([1000, 1010, 1013], [100.0, 99.7, 99.0])
    assert r[5] == ft.K_EOD                                                                  # 1013 < 1014 — исполнения ещё нет (конец данных)


def test_stop_trigger_price_boundary():
    r = run8([1000, 1010, 1020], [100.0, 99.81, 99.81])
    assert r[5] == ft.K_EOD                                                                  # выше S — не триггер
    r = run8([1000, 1010, 1020], [100.0, 100.0 * (1 - 0.002), 99.0])
    assert r[5] == ft.K_STOP and r[2] == 1020                                                # цена ≤ S — триггер (равна S)


def test_take_between_trigger_and_exec_goes_first():
    r = run8([1000, 1010, 1012, 1014], [100.0, 99.7, 102.0, 99.0])                           # порядок ленты: тейк на 1012 раньше исполнения стопа на 1014
    assert r[2] == 1012 and r[4] == TK and r[5] == ft.K_TAKE and r[6] == 0


def test_ambiguous_exec_trade_exit_first_and_flagged():
    r = run8([1000, 1010, 1014], [100.0, 99.7, 102.0])                                       # на сделке исполнения стопа цена выше Tk
    assert r[5] == ft.K_STOP and np.isclose(r[4], 102.0 * (1.0 - SLIP)) and r[6] == 1        # выход идёт первым, спорная сделка отмечена


def test_e_le_s_not_executed():
    r = run8([1000, 1010], [100.0, 100.0], lp=200.0)                                         # S = 199,6 > e
    assert r[0] == ft.CODE_E_LE_S
    assert ft.e8_money(r[0], r[3], r[4], r[5]) == (0.0, 0.0)


def test_no_tape_after_t():
    r = run8([500, 600], [100.0, 100.0], T=1000)
    assert r[0] == ft.CODE_NOTAPE


def test_time_exit_trigger_and_exec():
    r = run8([1000, 1050, 1110, 1112, 1114, 1120], [100.0, 100.0, 100.2, 100.25, 100.3, 100.4], time_ms=100)    # триггер — первая сделка ≥ 1100 (1110), исполнение — ≥ 1114
    assert r[5] == ft.K_TIME and r[2] == 1114 and np.isclose(r[4], 100.3 * (1.0 - SLIP))


def test_end_of_data_close():
    r = run8([1000, 1010, 1020], [100.0, 100.1, 100.2])
    assert r[0] == 0 and r[5] == ft.K_EOD and r[2] == 1020 and np.isclose(r[4], 100.2 * (1.0 - SLIP))


def test_stop_from_entry_in_natr():
    r = run8([1000, 1010, 1020, 1030], [100.0, 99.6, 99.55, 99.0], kind=1, par=0.5)          # S = e·(1 − 0,5·0,01) = 99,51: 99,6 не триггер
    assert r[5] == ft.K_EOD and r[2] == 1030                                                 # триггер на 1030 (99,0) без последующей сделки → конец данных
    r = run8([1000, 1010, 1020], [100.0, 99.5, 99.0], kind=1, par=0.5)
    assert r[5] == ft.K_STOP and r[2] == 1020                                                # триггер на 1010 (99,5 ≤ 99,51), исполнение 1020


def test_neighbor_take_mult_2():
    t2 = E * (1.0 + 2.0 * 0.01)
    r = run8([1000, 1010, 1020], [100.0, TK + 0.01, t2 + 0.01], tk=2.0)
    assert r[5] == ft.K_TAKE and r[2] == 1020 and r[4] == t2


def test_money_formulas():
    g, f = ft.e8_money(ft.CODE_OK, 100.0, 101.0, ft.K_TAKE)
    assert np.isclose(g, 5.0) and np.isclose(f, 500 * TAKER + 500 * 1.01 * MAKER)            # тейк — maker на выходе
    g, f = ft.e8_money(ft.CODE_OK, 100.0, 99.0, ft.K_STOP)
    assert np.isclose(g, -5.0) and np.isclose(f, 500 * TAKER + 500 * 0.99 * TAKER)           # остальное — taker
    assert ft.e8_money(ft.CODE_NOTAPE, 0.0, 0.0, 0) == (0.0, 0.0)


# ---- минутки и признаки места цены ---------------------------------------------------------------------------------------------------

N = 12000
T_OK = 11000


def minutes(n=N):
    i = np.arange(n, dtype=np.int64)
    c = 100000 + 10 * i
    return dict(o=c.copy(), h=c + 5, l=c - 5, c=c.copy(), qv=np.full(n, 1000, dtype=np.int64))


def test_place_features_formulas_long_short():
    a = minutes()
    m = ft.Mins(a)
    c = a["c"].astype(float)
    t = T_OK
    x24, x7, xp, xv = ft.place_features(m, t, +1, 0.01)
    assert np.isclose(x24, c[t - 1] / c[t - 1441] - 1.0) and np.isclose(x7, c[t - 1] / c[t - 1 - 10080] - 1.0)
    hi, lo = a["h"][t - 1440:t].max(), a["l"][t - 1440:t].min()
    assert hi == a["h"][t - 1] and lo == a["l"][t - 1440]
    assert np.isclose(xp, (c[t - 1] - lo) / (hi - lo))
    vwap = 1440.0 / (1.0 / c[t - 1440:t]).sum()                                          # Σ qv / Σ (qv / tp), qv и tp = c постоянны
    assert np.isclose(xv, (c[t - 1] / vwap - 1.0) / 0.01)
    y24, y7, yp, yv = ft.place_features(m, t, -1, 0.01)
    assert np.isclose(y24, -x24) and np.isclose(y7, -x7) and np.isclose(yv, -xv) and np.isclose(xp + yp, 1.0)    # шорт: знак и зеркало положения


def test_place_features_nan_cases():
    a = minutes()
    assert all(np.isnan(v) for v in ft.place_features(ft.Mins(a), 1000, 1, 0.01))             # нет суток истории
    assert all(np.isnan(v) for v in ft.place_features(ft.Mins(a), T_OK, 1, 0.0))              # NATR не определён
    x = ft.place_features(ft.Mins(a), 5000, 1, 0.01)
    assert np.isnan(x[1]) and all(np.isfinite(x[i]) for i in (0, 2, 3))                       # нет недели — только X7 пусто
    b = minutes()
    b["o"][T_OK - 1400:T_OK - 900] = 0                                                        # 500 дыр → данных 941 < 1 000
    assert all(np.isnan(v) for v in ft.place_features(ft.Mins(b), T_OK, 1, 0.01))
    b = minutes()
    b["o"][T_OK - 1400:T_OK - 1000] = 0                                                       # 400 дыр → 1 041 ≥ 1 000
    assert all(np.isfinite(v) for v in ft.place_features(ft.Mins(b), T_OK, 1, 0.01))


def test_place_features_causal_garbage():
    rng = np.random.default_rng(668)
    a = minutes()
    a["o"][T_OK - 3] = 0                                                                      # дыра перед t — ffill
    for t in (T_OK, 5000, 2000):
        g = ft.garbage_from(a, t, rng)
        for s in (1, -1):
            assert eq(ft.place_features(ft.Mins(a), t, s, 0.01), ft.place_features(ft.Mins(g), t, s, 0.01))


def test_garbage_from_structure():
    a = minutes()
    g = ft.garbage_from(a, 6000, np.random.default_rng(1))
    for k in a:
        assert np.array_equal(a[k][:6000], g[k][:6000])                                       # до t не тронуто
    assert not np.array_equal(a["c"][6000:], g["c"][6000:])
    assert (g["o"][6000:] >= 1).all() and (g["c"][6000:] >= 1).all() and (g["qv"][6000:] > 0).all()
    assert (g["h"] >= np.maximum(g["o"], g["c"])).all() and (g["l"] <= np.minimum(g["o"], g["c"])).all()


def test_ffill_close_holes():
    o = np.array([0, 5, 0, 7, 0])
    c = np.array([0, 10, 0, 20, 0])
    f = ft.ffill_close(o, c)
    assert np.isnan(f[0]) and f[1] == 10 and f[2] == 10 and f[3] == 20 and f[4] == 20


def test_load_b42_full_roundtrip():
    a = minutes(50)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "X.b42")
        with open(p, "wb") as f:
            f.write(b"B42\x01" + (50).to_bytes(4, "little"))
            f.write(np.stack([a["o"], a["h"], a["l"], a["c"], a["qv"]]).astype("<i8").tobytes())
        r = ft.load_b42_full(p)
        assert all(np.array_equal(r[k], a[k]) for k in a)
        with open(p, "wb") as f:
            f.write(b"XXXX" + (0).to_bytes(4, "little"))
        try:
            ft.load_b42_full(p)
            assert False
        except ValueError:
            pass


# ---- шок BTC ---------------------------------------------------------------------------------------------------------------------

def btc(n=8000, seed=3):
    cf = 50000.0 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0.0, 0.001, n)))
    return cf, ft.minute_returns(cf)


def test_btc_shock_formula_and_sigma_window():
    cf, r = btc()
    t = 5000
    for h in ft.HORIZONS:
        rh, sg = ft.btc_shock(cf, r, t, h)
        b = t - 1 - h
        assert np.isclose(rh, cf[t - 1] / cf[b] - 1.0)
        assert np.isclose(sg, r[b - 1440:b + 1].std(ddof=1) * np.sqrt(h))                     # 1 441 доходность строго до начала интервала
        cf2 = cf.copy()
        cf2[b + 1:] *= 1.3                                                                     # что внутри интервала — σ не меняет
        rh2, sg2 = ft.btc_shock(cf2, ft.minute_returns(cf2), t, h)
        assert sg2 == sg and rh2 != rh


def test_btc_shock_causal_and_nan():
    cf, r = btc()
    t = 5000
    cf2 = cf.copy()
    cf2[t:] = 1.0
    for h in ft.HORIZONS:
        assert eq(ft.btc_shock(cf, r, t, h), ft.btc_shock(cf2, ft.minute_returns(cf2), t, h))
        assert all(np.isnan(v) for v in ft.btc_shock(cf, r, 1000, h))                         # нет суток до интервала
    cf3 = cf.copy()
    cf3[3000:3600] = np.nan                                                                    # дыры → < 1 000 валидных доходностей
    assert all(np.isnan(v) for v in ft.btc_shock(cf3, ft.minute_returns(cf3), 4000, 5))


# ---- уровни и место до следующего (E5) ---------------------------------------------------------------------------------------------

def levels_fixture():
    sl = ft.SymLevels()
    big = ft.U32_MAX
    sl.add(0, big, [(0, 100)])                                                                # 0 — свой
    sl.add(0, big, [(0, 110), (50, 108)])                                                     # 1 — цена с 50-й минуты 108
    sl.add(0, 40, [(0, 105)])                                                                 # 2 — умер на 40-й
    sl.add(0, big, [(0, -90)])                                                                # 3 — |цена| 90
    sl.add(200, big, [(200, 101)])                                                            # 4 — родится на 200-й
    sl.freeze()
    return sl


def test_room_to_next_direction_alive_version():
    sl = levels_fixture()
    r, nx, own = ft.room_to_next(sl, 0, 100, 100, +1, 0.01)
    assert nx == 108 and np.isclose(r, 8 / (100 * 0.01)) and own                              # выше: версия 108, мёртвый 105 и неродившийся 101 не в счёт
    r, nx, own = ft.room_to_next(sl, 0, 100, 100, -1, 0.01)
    assert nx == 90 and np.isclose(r, 10 / (100 * 0.01)) and own                              # шорт — ближайший ниже, |−90|
    r, nx, own = ft.room_to_next(sl, 0, 30, 100, +1, 0.01)
    assert nx == 105 and np.isclose(r, 5.0)                                                    # на 30-й минуте 105 жив, 110 дальше
    r, nx, own = ft.room_to_next(sl, 0, 250, 100, +1, 0.01)
    assert nx == 101 and np.isclose(r, 1.0)                                                    # родившийся уровень виден
    r, nx, own = ft.room_to_next(sl, 0, 100, 200, +1, 0.01)
    assert np.isinf(r) and nx is None                                                          # следующего нет → +∞
    _, _, own = ft.room_to_next(sl, 0, 100, 99, +1, 0.01)
    assert not own                                                                             # цена версии ≠ сигнальной — «свой уровень» не найден


def test_parse_levels_members_with_commas():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "levels.csv")
        with open(p, "w", encoding="utf-8") as f:
            f.write("sym,level,born,end,first_bar,segs,members\n")
            f.write("AAA,0,10,4294967295,5,10:100|50:-108,a;b,c\n")
            f.write("AAA,1,20,60,7,20:90,x,y,z\n")
            f.write("BBB,0,0,100,0,0:7,\n")
        lv = ft.parse_levels(p)
        a = lv["AAA"]
        assert a.b.tolist() == [10, 20] and a.e.tolist() == [ft.U32_MAX, 60]
        assert a.px_at(0, 49) == 100 and a.px_at(0, 50) == 108 and a.px_at(0, 9) is None       # цена по версиям, |знак|
        assert lv["BBB"].px_at(0, 3) == 7
        with open(p, "w", encoding="utf-8") as f:
            f.write("sym,level,born,end,first_bar,segs,members\nAAA,1,0,1,0,0:1,\n")
        try:
            ft_levels = ft.parse_levels(p)
            assert False, ft_levels
        except ValueError:
            pass


# ---- клетки, маски, допуск ------------------------------------------------------------------------------------------------------------

def test_cell_specs_54_cells_18_trials():
    sp = cl.cell_specs()
    assert len(sp) == 54 and len({s["cid"] for s in sp}) == 54
    trial = [s for s in sp if s["trial"]]
    assert len(trial) == 18 and sum(1 for s in trial if s["family"] == "E") == 9 and sum(1 for s in trial if s["family"] == "B") == 9
    assert all(s["group"] == s["cid"] for s in trial)
    ids = {s["cid"] for s in trial}
    assert all(s["group"] in ids for s in sp)
    for s in trial:
        assert sum(1 for x in sp if not x["trial"] and x["group"] == s["cid"]) == 2           # у каждой пробной по два соседа
    assert {s["fam"] for s in sp} == {"E5", "EP", "B"}
    h = {s["cid"]: s for s in sp}
    assert h["XVH"]["op"] == "gt" and h["XVH"]["q"] == 0.75 and h["XVL"]["op"] == "lt" and h["XVL"]["q"] == 0.25
    assert h["XVH_q90"]["q"] == 0.90 and h["XVH_q60"]["q"] == 0.60 and h["XVL_q10"]["q"] == 0.10 and h["XVL_q40"]["q"] == 0.40
    assert h["Bsk_5"]["x"] == 2.0 and h["Bsk_5_k15"]["x"] == 1.5 and h["Bsk_5_k30"]["x"] == 3.0 and h["Bsk_15"]["col"] == "bz15"
    assert h["RM10"]["x"] == 1.0 and h["RM05"]["x"] == 0.5 and h["RM20"]["x"] == 2.0


def test_excl_mask_ops_nan_passes():
    v = np.array([0.5, 1.0, 2.0, NAN, np.inf])
    assert cl.excl_mask(dict(fam="E5", col="R", op="lt", x=1.0, q=None), v, {}).tolist() == [True, False, False, False, False]
    thr = {"X24": {"0.75": 0.3, "0.25": -0.3}}
    w = np.array([0.2, 0.3, 0.4, NAN])
    assert cl.excl_mask(dict(fam="EP", col="X24", op="gt", x=None, q=0.75), w, thr).tolist() == [False, False, True, False]
    assert cl.excl_mask(dict(fam="EP", col="X24", op="lt", x=None, q=0.25), np.array([-0.4, -0.3, 0.0, NAN]), thr).tolist() == [True, False, False, False]
    z = np.array([-2.0, -1.9, 2.0, 3.0, NAN])
    assert cl.excl_mask(dict(fam="B", col="bz5", op="abs", x=2.0, q=None), z, {}).tolist() == [True, False, True, True, False]
    assert cl.excl_mask(dict(fam="B", col="bz5", op="neg", x=2.0, q=None), z, {}).tolist() == [True, False, False, False, False]
    assert cl.excl_mask(dict(fam="B", col="bz5", op="pos", x=2.0, q=None), z, {}).tolist() == [False, False, True, True, False]


def test_admit_floor_rules():
    assert cl.admit_floor([30, 30, 30, 30, 12], 40, 200) == (False, True)
    assert cl.admit_floor([30, 30, 30, 30, 12], 29, 200) == (False, False)                    # исключено < 30
    assert cl.admit_floor([19, 30, 30, 30, 12], 40, 200) == (False, False)                    # в месяце < 20
    assert cl.admit_floor([30, 30, 30, 30, 7], 40, 200) == (False, False)                     # 01–13.11 < 8
    assert cl.admit_floor([20, 20, 20, 20, 10], 400, 1000) == (True, True)                    # подмножество: ≥ 70 и ≥ 8 в месяце
    assert cl.admit_floor([20, 20, 20, 20, 7], 400, 1000) == (True, False)
    assert cl.admit_floor([10, 10, 10, 10, 10], 400, 1000) == (True, False)                   # сумма 50 < 70


def test_quantile_table_linear_finite():
    v = np.arange(101, dtype=float)
    v = np.concatenate([v, [NAN, np.inf]])
    t = cl.quantile_table({c: v for c in cl.EP_COLS})
    assert t["X24"]["0.25"] == 25.0 and t["XV"]["0.90"] == 90.0 and t["XP"]["n"] == 101       # конечные значения; inf не входит


def test_cell_label_ru():
    for s in cl.cell_specs():
        assert cl.cell_label(s)


# ---- лента ------------------------------------------------------------------------------------------------------------------------

def test_load_tape_two_days_sorted():
    import pandas as pd
    d0 = datetime.datetime(2026, 7, 1, tzinfo=datetime.timezone.utc)
    ms0 = int(d0.timestamp() * 1000)
    with tempfile.TemporaryDirectory() as d:
        for k, rows in enumerate([[(ms0 + 30, 2.0), (ms0 + 10, 1.0)], []]):
            if not rows:
                continue
            df = pd.DataFrame({"agg_trade_id": range(len(rows)), "price": [r[1] for r in rows], "quantity": 1.0, "transact_time": [r[0] for r in rows]})
            with zipfile.ZipFile(os.path.join(d, "AAAUSDT-aggTrades-%s.zip" % (d0 + datetime.timedelta(days=k)).date().isoformat()), "w") as z:
                z.writestr("x.csv", df.to_csv(index=False))
        t, p, found = cl.load_tape(d, "AAAUSDT", ms0)
        assert found == 1 and t.tolist() == [ms0 + 10, ms0 + 30] and p.tolist() == [1.0, 2.0]    # один день из двух, внутри дня — по времени
        t, p, found = cl.load_tape(d, "AAAUSDT", ms0 + 5 * 86400_000)
        assert found == 0 and len(t) == 0


# ---- E4: независимый пересчёт уровней источника (`tk668_e4.src_levels`) ----------------------------------------------------------------

def e4_minutes(days, h=100, l=100, c=100, qv=1):
    n = days * 1440
    return {k: np.full(n, v, dtype=np.int64) for k, v in (("o", 100), ("h", h), ("l", l), ("c", c), ("qv", qv))}


def test_e4_lw():
    import tk668_e4 as e4

    class W:
        def __init__(self, t0):
            self.t0 = t0
    assert e4.lw_of(W("2026-06-17")) == 5       # среда → понедельник 22.06
    assert e4.lw_of(W("2026-06-22")) == 0       # понедельник
    assert e4.lw_of(W("2026-06-21")) == 1       # воскресенье
    assert e4.lw_of(W("2026-07-25")) == 2       # суббота → 27.07
    assert e4.lw_of(W("2026-09-01")) == 6       # вторник → 07.09


def test_e4_day_long_short():
    import tk668_e4 as e4
    a = e4_minutes(3)
    a["h"][5 * 60 + 17] = 500                   # сутки 0, час 5: максимум
    a["l"][7 * 60 + 3] = 50                     # сутки 0, час 7: минимум
    lv = e4.src_levels(a, 1, 1, 0)
    assert lv[0] == (24 * 60, 48 * 60, 5, 500) and len(lv) == 2     # родился в 24:00, живёт сутки; последние сутки без «следующих» — уровня нет
    sv = e4.src_levels(a, -1, 1, 0)
    assert sv[0] == (24 * 60, 48 * 60, 7, 50)
    a["c"][30 * 60 + 59] = 40                   # час 30 (сутки 1) закрылся ниже минимума суток 0 → шорт-уровень умер в конце этого часа
    sv = e4.src_levels(a, -1, 1, 0)
    assert sv[0] == (24 * 60, 31 * 60, 7, 50)
    a["c"][27 * 60 + 59] = 600                  # лонг: закрытие часа 27 выше максимума — конец уровня 28:00
    lv = e4.src_levels(a, 1, 1, 0)
    assert lv[0] == (24 * 60, 28 * 60, 5, 500)


def test_e4_hole_hour_kills_level():
    import tk668_e4 as e4
    a = e4_minutes(3)
    a["o"][10 * 60:11 * 60] = 0                 # час 10 без данных → суток 0 уровня нет
    lv = e4.src_levels(a, 1, 1, 0)
    assert [x[0] for x in lv] == [48 * 60]


def test_e4_week_offset():
    import tk668_e4 as e4
    a = e4_minutes(16)
    a["h"][2 * 1440 + 100] = 900                # день 2 (до первого понедельника при lw = 3)
    a["h"][5 * 1440 + 100] = 700                # день 5 — внутри недели 3…9
    lv = e4.src_levels(a, 1, 2, 3)
    assert lv[0][0] == 10 * 1440 and lv[0][3] == 700 and lv[0][2] == 5 * 24 + 1     # неделя дни 3…9 → родилась в день 10
    assert len(lv) == 1                                                             # следующая неделя (10…16) не закрыта


def test_e4_profile_poc():
    import tk668_e4 as e4
    a = e4_minutes(3, h=1_000_000, l=1_000_000, c=1_000_000, qv=1)
    a["h"][100:200] = a["l"][100:200] = a["c"][100:200] = 1_010_000
    a["qv"][100:200] = 100
    a["qv"][150] = 500                          # пик корзины — минута 150 (час 2)
    lv = e4.src_levels(a, 1, 3, 0)
    assert lv[0][3] == 1010 * 1000 + 500 and lv[0][2] == 150 // 60 and lv[0][0] == 24 * 60
    sv = e4.src_levels(a, -1, 3, 0)
    assert sv[0][3] == lv[0][3]                 # профиль не зависит от стороны (цена по модулю)
    a["o"][:300] = 0                            # меньше 1 200 минут с данными → профиля суток 0 нет
    assert all(x[0] != 24 * 60 for x in e4.src_levels(a, 1, 3, 0))


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print("ok   ", name)
        except Exception:  # noqa: BLE001
            fails += 1
            print("FAIL ", name)
            traceback.print_exc()
    print("%d из %d зелёных" % (len(tests) - fails, len(tests)))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
