"""Тесты автомата перетитровки §13.24 (retitr_full.py) на синтетике — без движка, без P&L.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_retitr_full.py` — выполняет все test_* и печатает итог.
"""
import datetime
import hashlib
import io
import json
import os
import sys
import tempfile
import traceback
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import retitr_cells as rc  # noqa: E402
import retitr_full as rf  # noqa: E402

GRIDS = os.path.join(HERE, "..", "..", "..", "docs", "research", "freeze", "tk599-grids.json")
NB = 1500                      # повторов бутстрепа в тестах (в боевом запуске 20 000)
SPEC = rf.Spec.load(GRIDS)


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return str(e)
    raise AssertionError("ожидалось %s" % exc.__name__)


# ---- синтетический мир: общие сделки, эффект ключей --------------------------------------------------------------------------

class World:
    """Сделки одни и те же у всех конфигураций (6 в сутки); val = база + эффект(ключи) + шум по хэшу прогона. effect(keys, sig, day) → сдвиг ($ на сделку)."""

    def __init__(self, effect=None, ncoins=12, per_day=6, noise=0.3, seed=7, fb=None):
        r = np.random.default_rng(seed)
        self.day = np.repeat(np.arange(rf.NDAYS), per_day)
        self.sym = r.integers(0, ncoins, size=len(self.day))
        self.base = r.normal(0.2, 3.0, len(self.day))
        self.effect, self.noise, self.fbv, self.calls = effect or (lambda k, s, d: 0.0), noise, fb, 0
        self.ncoins = ncoins

    def __call__(self, req):
        self.calls += 1
        plain, feats = rc.parse_keys(req.keys)
        k = dict(plain)
        if feats:
            k.update(feats[-1])
        sh = np.broadcast_to(np.asarray(self.effect(k, req.sig, self.day), dtype=float), self.day.shape)
        # шум — функция эффекта: конфигурации с одинаковым эффектом идентичны (Δ = 0 точно), без эффекта шума нет
        noise = np.random.default_rng(int(hashlib.md5(sh.tobytes()).hexdigest()[:8], 16)).normal(0, self.noise, len(self.day)) if np.any(sh != 0) else 0.0
        fb = None if self.fbv is None else self.fbv(self.day)
        return rf.make_series(self.day, self.sym, self.base + sh + noise, fb=fb)


def ev_for(world):
    return rf.Evaluator(world)


def run_gens(gens, ev):
    return rf.drive(gens, ev)


def one(gen, ev):
    return rf.drive({"x": gen}, ev)["x"]


def axis_of(side, name):
    return next(a for a in SPEC.axes[side] if a["axis"] == name)


def step(world, side, name, cfg=None):
    ev = ev_for(world)
    om = rf.month_days(range(5))
    return one(rf.eval_axis(ev, SPEC, side, axis_of(side, name), cfg or {}, om, world.ncoins, NB), ev), ev


# ---- окна, склейка, охрана -----------------------------------------------------------------------------------------------------

def rows_for(win, entries):
    """entries: (монета, дата входа, gross, fees, funding, kind) → строки trades.csv движка."""
    out = []
    for sym, date, gross, fees, fund, kind in entries:
        d = datetime.date.fromisoformat(date)
        e = (d - win.t0d).days * rf.DAY_MS + 3_600_000
        out.append(dict(sym=sym, entry_ms=e, exit_ms=e + 600_000, gross=gross, fees=fees, funding=fund, net=gross - fees + fund, kind=kind))
    return out


def daily_for(win, rows):
    d = {}
    for r in rows:
        i = r["entry_ms"] // rf.DAY_MS - rf.GRID_START_DAY
        d[i] = d.get(i, 0.0) + r["net"]
    return d


def tables(entries_by_win):
    return {w.name: rf.table_from_rows(rows_for(w, entries_by_win.get(w.name, []))) for w in rf.WINDOWS}


def test_check_windows():
    rf.check_windows()
    assert "P42_T0" in raises(rf.GuardError, rf.check_windows, rf.WINDOWS, {"wa": "2026-06-17", "wb": "2026-08-11", "wc": "2026-10-05"})
    gap = (rf.WINDOWS[0], rf.Window("wb", "2026-08-10", "2026-09-02", "2026-10-24"), rf.WINDOWS[2])
    assert "не смыкаются" in raises(rf.GuardError, rf.check_windows, gap)
    over = (rf.WINDOWS[0], rf.Window("wb", "2026-08-10", "2026-08-31", "2026-10-24"), rf.WINDOWS[2])
    assert "не смыкаются" in raises(rf.GuardError, rf.check_windows, over)
    short = (rf.WINDOWS[0], rf.WINDOWS[1], rf.Window("wc", "2026-10-05", "2026-10-25", "2026-11-12"))
    assert "покрывают" in raises(rf.GuardError, rf.check_windows, short)
    out = (rf.Window("wa", "2026-06-17", "2026-06-30", "2026-08-31"),) + rf.WINDOWS[1:]
    assert "вне торговой" in raises(rf.GuardError, rf.check_windows, out)


def test_splice_days_and_ranges():
    coins = ["AAA", "BBB"]
    ent = {"wa": [("AAA", "2026-07-01", 3.0, 0.5, 0.0, "stop"), ("BBB", "2026-08-31", 2.0, 0.5, 0.0, "tx"), ("AAA", "2026-09-02", 9.0, 0.5, 0.0, "stop")],   # хвост wa — вне зачёта
           "wb": [("AAA", "2026-08-28", 7.0, 0.5, 0.0, "stop"), ("BBB", "2026-09-01", 4.0, 0.5, 0.1, "be"), ("AAA", "2026-10-24", 1.0, 0.5, 0.0, "rt")],
           "wc": [("BBB", "2026-10-25", 5.0, 0.5, 0.0, "stop"), ("AAA", "2026-11-13", 6.0, 0.5, 0.0, "forced")]}
    t = tables(ent)
    s = rf.splice(t, coins)
    assert list(s.day) == [0, 61, 62, 115, 116, 135], list(s.day)          # 01.07, 31.08, 01.09, 24.10, 25.10, 13.11
    assert list(s.sym) == [0, 1, 1, 0, 1, 0]
    assert np.allclose(s.val, [2.5, 1.5, 3.5, 0.5, 4.5, 5.5])
    assert np.isclose(s.fund[2], 0.1)
    assert s.t_in[0] == int(datetime.datetime(2026, 7, 1, 1, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    assert rf.check_forced(t) == 1                                         # forced на 13.11 в wc допустим


def test_splice_guards():
    coins = ["AAA"]
    base = {"wa": [("AAA", "2026-07-03", 3.0, 0.5, 0.0, "stop")]}
    t = tables(base)
    t["wa"].net[0] += 0.01
    assert "net" in raises(rf.GuardError, rf.splice, t, coins)
    t = tables(base)
    assert "вне таблицы" in raises(rf.GuardError, rf.splice, t, ["ZZZ"])
    ent = {"wa": [("AAA", "2026-07-03", 3.0, 0.5, 0.0, "stop"), ("AAA", "2026-07-05", 1.0, 0.5, 0.0, "stop")]}
    t = tables(ent)
    daily = {w.name: daily_for(w, rows_for(w, ent.get(w.name, []))) for w in rf.WINDOWS}
    rf.splice(t, coins, daily=daily)
    daily["wa"][3 + 0] = daily["wa"].get(3, 0.0) + 0.5               # строка daily расходится с net сделок среза
    assert "daily" in raises(rf.GuardError, rf.splice, t, coins, daily=daily)
    forced = {"wa": [("AAA", "2026-07-03", 3.0, 0.5, 0.0, "forced")]}
    assert "G3b" in raises(rf.GuardError, rf.check_forced, tables(forced))


def test_overlap_gate():
    wa, wb = rf.WINDOWS[0], rf.WINDOWS[1]
    e = [("AAA", "2026-08-30", 3.0, 0.5, 0.0, "stop"), ("BBB", "2026-09-01", 2.0, 0.5, 0.0, "tx")]
    ta, tb = rf.table_from_rows(rows_for(wa, e)), rf.table_from_rows(rows_for(wb, e))
    assert rf.overlap_diff(ta, wa, tb, wb, "2026-08-27", "2026-09-03") == []
    e2 = [e[0], ("BBB", "2026-09-01", 2.1, 0.5, 0.0, "tx")]
    d = rf.overlap_diff(ta, wa, rf.table_from_rows(rows_for(wb, e2)), wb, "2026-08-27", "2026-09-03")
    assert len(d) == 1 and "gross" in d[0]
    d = rf.overlap_diff(ta, wa, rf.table_from_rows(rows_for(wb, e[:1])), wb, "2026-08-27", "2026-09-03")
    assert len(d) == 1 and "только в wa" in d[0]
    assert rf.overlap_diff(ta, wa, rf.table_from_rows(rows_for(wb, e[:1])), wb, "2026-08-27", "2026-08-31") == []     # вне диапазона сравнения


def test_funding_bound_and_loader():
    rates = {"AAA": (np.array([100, 200, 300], dtype=np.int64), np.array([0.01, 0.02, 0.03]))}
    b = rf.funding_bound(["AAA", "AAA", "AAA", "BBB"], np.array([100, 99, 300, 0]), np.array([200, 100, 400, 500]), rates)
    # (100, 200] → выплата 200; (99, 100] → 100; (300, 400] → ничего; BBB — нет ставок
    assert np.allclose(b, [500 * 0.02, 500 * 0.01, 0.0, 0.0]), b
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "AAA"))
        for m, rows in (("2026-07", [(1000, "-0.0004")]), ("2026-08", [(2000, "0.0002"), (500, "0.0001")])):
            with zipfile.ZipFile(os.path.join(d, "AAA", "AAA-fundingRate-%s.zip" % m), "w") as z:
                z.writestr("x.csv", "calc_time,funding_interval_hours,last_funding_rate\n" + "".join("%d,8,%s\n" % r for r in rows))
        r = rf.load_funding_rates(d, ["AAA", "NOPE"], months=("2026-07", "2026-08"))
        assert list(r["AAA"][0]) == [500, 1000, 2000] and np.allclose(r["AAA"][1], [0.0001, 0.0004, 0.0002]) and "NOPE" not in r


def test_splice_attaches_bound():
    wa = rf.WINDOWS[0]
    ent = {"wa": [("AAA", "2026-07-03", 1.0, 0.0, 0.0, "stop"), ("AAA", "2026-08-31", 1.0, 0.0, 0.0, "stop")],
           "wb": [("AAA", "2026-09-02", 1.0, 0.0, 0.0, "stop")]}
    t = tables(ent)
    t0 = rf._t0_ms(wa)
    rates = {"AAA": (np.array([t0 + (16 * rf.DAY_MS) + 1_800_000, t0 + 75 * rf.DAY_MS + 1_800_000]), np.array([0.01, 0.01]))}   # 03.07 00:30 (до входа 01:00) … 01.09 00:30
    s = rf.splice(t, ["AAA"], rates=rates)
    assert np.allclose(s.fb, 0.0)                                          # входы в 01:00 ушли на 10 минут: выплат 00:30 не пересекают
    rates = {"AAA": (np.array([rf._t0_ms(wa) + 16 * rf.DAY_MS + 3_600_000 + 300_000]), np.array([0.01]))}                    # 03.07 01:05 — внутри сделки
    s = rf.splice(t, ["AAA"], rates=rates)
    assert np.allclose(s.fb, [5.0, 0.0, 0.0]), s.fb
    ent["wb"] = [("AAA", "2026-09-02", 1.0, 0.0, 0.0, "stop")]
    rates = {"AAA": (np.array([rf._t0_ms(rf.WINDOWS[1]) + 23 * rf.DAY_MS + 3_600_000 + 300_000]), np.array([0.01]))}          # 02.09 01:05 — сентябрь: граница не нужна
    s = rf.splice(tables(ent), ["AAA"], rates=rates)
    assert s.fb[2] == 0.0


# ---- статистика ----------------------------------------------------------------------------------------------------------------

def test_seed_encoding():
    a = [np.array([1.0, -0.0]), np.array([2.0, 3.0]), np.array([0.1234567891, 4.0]), np.array([1.0, 1.0])]
    txt = "|".join(",".join("%.9f" % x for x in arr) for arr in [np.array([1.0, 0.0]), a[1], np.array([0.123456789, 4.0]), a[3]])
    assert rf.seed_of(*a) == int(hashlib.md5(txt.encode()).hexdigest()[:8], 16)
    assert rf.seed_of(*a) == rf.seed_of(*[x.copy() for x in a])
    assert rf.seed_of(a[1], a[0], a[2], a[3]) != rf.seed_of(*a)           # порядок массивов важен


def test_delta_stat():
    r = np.random.default_rng(3)
    n = 60
    Nc = Nr = r.integers(3, 9, n).astype(float)
    Sr = r.normal(0.1, 2, n) * Nr
    st = rf.delta_stat(Sr + 0.5 * Nr, Nc, Sr, Nr, NB)
    assert abs(st.d - 0.5) < 1e-12 and st.se < 1e-12 and st.z > 1e9 and st.dsum > 0 and st.n_days == n   # без шума SE ≈ 0
    Sc = Sr + 0.5 * Nc + r.normal(0, 3, n)
    a, b = rf.delta_stat(Sc, Nc, Sr, Nr, NB), rf.delta_stat(Sc, Nc, Sr, Nr, NB)
    assert a == b and a.se > 0 and a.lower < a.d and abs(a.z - a.d / a.se) < 1e-12
    assert abs(a.dsum - (Sc.sum() - Sr.sum())) < 1e-9
    assert a.n_c == int(Nc.sum())
    raises(rf.GuardError, rf.delta_stat, Sc, np.zeros(n), Sr, Nr, NB)
    s2 = rf.delta_stat(Sc[::-1].copy(), Nc[::-1].copy(), Sr[::-1].copy(), Nr[::-1].copy(), NB)      # другой порядок суток — другое зерно, та же Δ
    assert abs(s2.d - a.d) < 1e-12 and s2.se != a.se


def test_block_boot_covers_circular_blocks():
    idx = rf.block_idx(10, np.random.default_rng(1), 500)
    assert idx.shape == (500, 10) and idx.min() >= 0 and idx.max() < 10
    runs = [(np.diff(row[:4]) % 10 == 1).all() for row in idx]            # первые 4 — один круговой блок
    assert all(runs)


def test_month_rules():
    assert [rf.need_months(n) for n in (1, 2, 3, 4, 5)] == [0, 2, 2, 3, 4]
    Sc = np.zeros(rf.NDAYS)
    Nc = np.ones(rf.NDAYS) * 1.0
    om = rf.month_days(range(5))
    mt = rf.month_table(Sc, Nc, Sc, Nc, om)
    assert set(mt) == {0, 1, 2, 3, 4} and mt[4][1] == 13
    assert rf.min_trades_ok(mt)                                            # по 1 сделке в сутки: 30–31 ≥ 20 и 13 ≥ 8
    Nc2 = np.ones(rf.NDAYS)
    assert rf.min_trades_ok(rf.month_table(Sc, Nc2, Sc, Nc2, om)) is True
    Nc2[rf.month_days([4])[:6]] = 0                                       # 7 сделок в 01–13.11 < 8
    assert rf.min_trades_ok(rf.month_table(Sc, Nc2, Sc, Nc2, om)) is False
    Nc3 = np.ones(rf.NDAYS)
    Nc3[rf.month_days([0])[:12]] = 0                                      # 19 сделок в июле < 20
    assert rf.min_trades_ok(rf.month_table(Sc, Nc3, Sc, Nc3, om)) is False
    mt = {0: (1.0, 30, 30), 1: (1.0, 30, 30), 2: (-1.0, 30, 30)}
    assert rf.sign_ok(mt) and not rf.sign_ok({0: (1.0, 30, 30), 1: (-1.0, 30, 30)})


def test_robust_days_and_coins():
    day = np.repeat(np.arange(rf.NDAYS), 4)
    sym = np.tile(np.arange(4), rf.NDAYS)
    ref = rf.make_series(day, sym, np.zeros(len(day)))
    cell = rf.make_series(day, sym, np.full(len(day), 0.05))
    om = rf.month_days(range(5))
    ok, dd, dc = rf.robust(cell, ref, om, 4)
    assert ok and abs(dd - 0.05) < 1e-12 and abs(dc - 0.05) < 1e-12
    v = np.zeros(len(day))
    v[(day == 10) & (sym == 0)] = 40.0                                     # вся Δ — одни сутки у одной монеты
    ok, dd, dc = rf.robust(rf.make_series(day, sym, v), ref, om, 4)
    assert not ok
    v = np.zeros(len(day))
    v[sym == 0] = 0.5                                                      # вся Δ — одна монета, разнесённая по суткам
    ok, dd, dc = rf.robust(rf.make_series(day, sym, v), ref, om, 4)
    assert not ok and dd > 0 and dc <= 0
    v = np.zeros(len(day))
    v[np.isin(day, [3, 70])] = 9.0                                         # две лучших суток дают всё
    ok, dd, dc = rf.robust(rf.make_series(day, sym, v), ref, om, 4)
    assert not ok and dd is not None and dd <= 0


def test_funding_sensitivity_flips_step():
    eff = lambda k, s, d: 0.3 if k.get("rr") == "3" else 0.0              # noqa: E731
    st, _ = step(World(eff, noise=0.05), "L", "rr")
    assert st.accepted and st.winner == "rr_3", st.reasons
    fb = lambda d: np.where(d < 62, 0.4, 0.0)                              # июль–август: граница 0,4 $ на сделку → Δ' ≈ 0,3 − 2·0,4·62/136 < 0
    st, _ = step(World(eff, noise=0.05, fb=fb), "L", "rr")
    assert not st.accepted and "f_funding" in " ".join(st.reasons), st.reasons


# ---- шаг по оси -------------------------------------------------------------------------------------------------------------------

def test_axis_categorical_accept_and_reject():
    st, ev = step(World(lambda k, s, d: 1.5 if k.get("rr") == "3" else 0.0), "L", "rr")
    assert st.accepted and st.winner == "rr_3" and st.cell["patch"] == {"rr": "3"} and st.stat.z >= 2.5
    assert st.n_cells == 2 and {r["id"] for r in st.rows} == {"rr_1", "rr_3"}
    assert any(b >= 1 for b in ev.batches)
    st, _ = step(World(lambda k, s, d: 0.0), "L", "rr")                    # нет эффекта
    assert not st.accepted
    st, _ = step(World(lambda k, s, d: -1.0 if k.get("rr") == "3" else (-1.0 if k.get("rr") == "1" else 0.0)), "L", "rr")
    assert not st.accepted and st.winner in ("rr_1", "rr_3") and "победителя нет" in st.reasons[0]


def test_axis_threshold_z_categorical():
    # Δ ≈ 0,03 при SE ≈ 0,012: z ≈ 2,5 — граница; категориальная ось требует 2,5, упорядоченная 2
    st, _ = step(World(lambda k, s, d: 0.021 if k.get("rr") == "3" else 0.0, noise=0.3), "L", "rr")
    z = st.stat.z
    assert (z >= 2.5) == st.accepted or "b_z" in " ".join(st.reasons) or "g_cat" in " ".join(st.reasons)
    if z < 2.5:
        assert not st.accepted


def test_axis_veto_by_portfolio():
    eff = lambda k, s, d: (1.0 if s else -3.0) if k.get("rr") == "3" else 0.0   # noqa: E731  сигнальный двойник лучше, портфель хуже
    st, _ = step(World(eff), "L", "rr")
    assert not st.accepted and "h_veto" in " ".join(st.reasons)


def test_conditions_robustness_blocks_two_days():
    day = np.repeat(np.arange(rf.NDAYS), 3)
    sym = np.tile(np.arange(3), rf.NDAYS)
    ref = rf.make_series(day, sym, np.zeros(len(day)))
    v = np.where(np.isin(day, [5, 90]), 60.0, -0.05)                                  # плюс — только две сутки
    st, cond = rf.conditions(rf.make_series(day, sym, v), ref, rf.month_days(range(5)), 3, NB)
    assert cond["d_robust"][0] is False and st.d > 0
    st, cond = rf.conditions(rf.make_series(day, sym, np.full(len(day), 0.3)), ref, rf.month_days(range(5)), 3, NB)
    assert cond["d_robust"][0] is True and cond["e_sum"][0] and cond["f_funding"][0]


def test_axis_months_sign_rule():
    # эффект только в июле–октябре на 4 из 5 месяцев — проходит; на 3 из 5 — нет (нужно 4)
    eff4 = lambda k, s, d: np.where(d < 123, 1.0, -0.2) if k.get("rr") == "3" else 0.0    # noqa: E731
    st, _ = step(World(eff4), "L", "rr")
    assert st.accepted, st.reasons
    eff3 = lambda k, s, d: np.where(d < 92, 1.0, -0.4) if k.get("rr") == "3" else 0.0     # noqa: E731
    st, _ = step(World(eff3), "L", "rr")
    assert not st.accepted and "c_months" in " ".join(st.reasons)


def test_axis_ordered_plateau():
    # лонг, `s`: сетка 1; 1,5; 2; 3; 4; [5 база]; 6; 8; расширения 0,5 и 12
    def eff(vals):
        return lambda k, s, d: vals.get(k.get("s"), 0.0)
    st, _ = step(World(eff({"4": 1.0, "3": 0.8, "6": 0.8})), "L", "s")
    assert st.accepted and st.winner == "s_4", st.reasons                # соседи 3 и 6 (базу 5 пропускаем)
    st, _ = step(World(eff({"4": 1.0, "3": 0.8, "6": 0.2})), "L", "s")
    assert not st.accepted and "g_plateau" in " ".join(st.reasons)
    st, _ = step(World(eff({"6": 1.0, "4": 0.6, "8": 0.6})), "L", "s")   # слева от 6 — 4 (5 — текущее, пропущено)
    assert st.accepted and st.winner == "s_6"


def test_axis_edge_extension():
    def eff(vals):
        return lambda k, s, d: vals.get(k.get("s"), 0.0)
    # край 8 → расширение 12 как сосед; победитель — край
    st, _ = step(World(eff({"8": 1.0, "6": 0.7, "12": 0.7})), "L", "s")
    assert st.accepted and st.winner == "s_8" and st.n_cells == 7 + 1
    # расширение лучше края — ось не меняется, расширение победителем не бывает
    st, _ = step(World(eff({"8": 1.0, "6": 0.7, "12": 1.4})), "L", "s")
    assert not st.accepted and st.winner == "s_8" and "расширение s_12 лучше края" in " ".join(st.reasons), st.reasons
    # нижний край 1 → расширение 0,5
    st, _ = step(World(eff({"1": 1.0, "1.5": 0.7, "0.5": 0.7})), "L", "s")
    assert st.accepted and st.winner == "s_1"


def test_axis_e_neighbours_after_critic_edit():
    def eff(vals):
        return lambda k, s, d: vals.get(k.get("e"), 0.0)
    L = axis_of("L", "e")
    assert [c["patch"]["e"] for c in L["cells"]] == ["0.05", "0.1", "0.15", "0.2"] and L["ext"] == {}
    S = axis_of("S", "e")
    assert [c["patch"]["e"] for c in S["cells"]] == ["0.05", "0.1", "0.3"] and S["ext"]["high"]["patch"] == {"e": "0.5"} and "low" not in S["ext"]
    st, _ = step(World(eff({"0.15": 1.0, "0.1": 0.7, "0.2": 0.7})), "L", "e")      # у 0,15 два соседа (0,1 и 0,2)
    assert st.accepted and st.winner == "e_0.15", st.reasons
    st, _ = step(World(eff({"0.2": 1.0, "0.15": 0.9})), "L", "e")                  # у 0,2 соседа сверху нет (0,3 — текущее, расширений нет)
    assert not st.accepted and "соседей" in " ".join(st.reasons)
    st, _ = step(World(eff({"0.05": 1.0, "0.1": 0.9})), "L", "e")                  # край 0,05 без расширения
    assert not st.accepted
    st, _ = step(World(eff({"0.3": 1.0, "0.1": 0.7, "0.5": 0.7})), "S", "e")       # шорт: 0,3 — край, сосед сверху — расширение 0,5
    assert st.accepted and st.winner == "e_0.3", st.reasons
    st, _ = step(World(eff({"0.1": 1.0, "0.05": 0.7, "0.3": 0.7})), "S", "e")      # 0,1: соседи 0,05 и 0,3 (0,15 — текущее)
    assert st.accepted and st.winner == "e_0.1"


def test_axis_natr_min_ordered_edges():
    def eff(vals):
        return lambda k, s, d: vals.get(k.get("natr_min"), 0.0)
    st, _ = step(World(eff({"0.3": 1.0, "0.2": 0.7, "0.6": 0.7})), "L", "natr_min")    # соседи 0,2 и 0,6 (0,4 — база, пропущена)
    assert st.accepted and st.winner == "natr_min_0.3", st.reasons
    st, _ = step(World(eff({"0.8": 1.0, "0.6": 0.9})), "L", "natr_min")
    assert not st.accepted                                                              # 0,8 — край без расширения
    assert axis_of("L", "natr_min")["mode"] == "port"


def test_axis_skips_cells_equal_to_ref():
    spec = rf.Spec({"bases": {"L": rc.BASES["L"]}, "base_values": {"L": {"s": 5, "x": 0}},
                    "sides": {"L": [{"axis": "x", "group": 1, "mode": "port", "class": "categorical",
                                      "cells": [{"id": "x_5", "patch": {"s": "5"}}, {"id": "x_3", "patch": {"s": "3"}}], "order": [], "ext": {}}]},
                    "features": {"L": []}})
    w = World(lambda k, s, d: 1.0 if k.get("s") == "3" else 0.0)
    ev = ev_for(w)
    st = one(rf.eval_axis(ev, spec, "L", spec.axes["L"][0], {}, rf.month_days(range(5)), w.ncoins, NB), ev)
    assert [r["id"] for r in st.rows] == ["x_3"], st.rows               # `s=5` равна базе по эффективным ключам — не клетка


def test_pass2_base_is_candidate_and_neighbours_skip_current():
    w = World(lambda k, s, d: {"3": 1.0, "2": 0.5, "4": 0.5}.get(k.get("s"), 0.0))
    ev = ev_for(w)
    om = rf.month_days(range(5))
    ax = axis_of("L", "s")
    cfg = {"s": next(c for c in ax["cells"] if c["id"] == "s_3")}
    pts = rf.axis_points(SPEC, "L", ax, cfg)
    cur = [p.cid for p in pts if p.current]
    assert cur == ["s_3"]
    p2 = next(p for p in pts if p.cid == "s_2")
    left, right = rf.neighbours(pts, p2)
    assert left.cid == "s_1.5" and right.cid == "s_4"                   # текущее 3 пропущено
    pb = next(p for p in pts if p.cid == "base")
    left, right = rf.neighbours(pts, pb)
    assert left.cid == "s_4" and right.cid == "s_6"                      # база (5) — обычная точка
    st = one(rf.eval_axis(ev, SPEC, "L", ax, cfg, om, w.ncoins, NB), ev)
    assert "base" in {r["id"] for r in st.rows} and "s_3" not in {r["id"] for r in st.rows}
    assert not st.accepted                                               # текущее лучшее: Δ всех ≤ 0


def test_frozen_grid_consistency():
    for side, total, ext in (("L", 54, 2), ("S", 58, 3)):
        n = sum(len(a["cells"]) for a in SPEC.axes[side]) + len(SPEC.features(side)) * 3
        assert n == total, (side, n)
        assert sum(1 for a in SPEC.axes[side] for k in ("low", "high") if a["ext"].get(k)) == ext
        for a in SPEC.axes[side]:
            if a["class"] != "ordered":
                continue
            pts = rf.axis_points(SPEC, side, a, {})
            for p in pts:
                if p.ext or p.current:
                    continue
                left, right = rf.neighbours(pts, p)
                got = [q.cid for q in (left, right) if q is not None]
                assert got == a["neighbours"][p.cid], (side, a["axis"], p.cid, got, a["neighbours"][p.cid])
    assert sorted(g for g in {a["group"] for a in SPEC.axes["L"]}) == [1, 2, 3, 4, 5]
    assert rf.cfg_keys(SPEC, "S", {}) == rc.merge_keys(rc.BASES["S"])


def test_cfg_keys_order_later_axis_wins_and_hash():
    ns = next(c for c in axis_of("S", "ns")["cells"] if c["id"] == "ns_geo")          # sn=3 sg=2 sr=1.5 bm=1
    bm = next(c for c in axis_of("S", "bm")["cells"] if c["id"] == "bm_2")
    k = rf.cfg_keys(SPEC, "S", {"bm": bm, "ns": ns})
    assert dict(rc.parse_keys(k)[0])["bm"] == "2" and "sn=3" in k                       # bm (группа 4) после ns (группа 1)
    a, b = rf.Req("L", "rr=3 s=4", False), rf.Req("L", "s=4 rr=3", False)
    assert a.hash == b.hash and a.hash != rf.Req("L", "rr=3 s=4", True).hash != rf.Req("S", "rr=3 s=4", False).hash
    assert rf.Req("L", "s=5", False).hash == rf.Req("L", "", False).hash                # значение по умолчанию = ключ не задан
    assert rc.SIGMODE.split()[0] in rf.Req("L", "s=4", True).engine_keys and "p=100000" not in rf.Req("L", "s=4", False).engine_keys


# ---- проходы, совместная сетка, свёртки ---------------------------------------------------------------------------------------------

def test_pass_sequential_reference_update():
    # `bm=2` хорош сам по себе; `rr=3` хорош, только если в эталоне уже есть bm=2 (эффект идёт через принятое значение)
    def eff(k, s, d):
        sh = 1.0 if k.get("bm") == "2" else 0.0
        if k.get("rr") == "3" and k.get("bm") == "2":
            sh += 1.0
        return sh
    w = World(eff)
    ev = ev_for(w)
    om = rf.month_days(range(5))
    cfg, steps = one(rf.run_pass(ev, SPEC, "L", {}, om, w.ncoins, nboot=NB), ev)
    assert set(cfg) == {"bm", "rr"} and cfg["bm"]["id"] == "bm_2" and cfg["rr"]["id"] == "rr_3"
    acc = [s.axis for s in steps if s.accepted]
    assert acc == ["bm", "rr"]
    assert [s.axis for s in steps][:3] == ["s", "ls", "ns"]                              # порядок групп из спека


def test_pass2_not_converged_gives_base():
    # `s=3` хорош только при bm=2 (группа 4 идёт позже s): проход 1 принимает bm, проход 2 — уже s ⇒ «не сошлось»
    def eff(k, s, d):
        sh = 1.0 if k.get("bm") == "2" else 0.0
        if k.get("s") == "3" and k.get("bm") == "2":
            sh += 1.0
        if k.get("s") == "4" and k.get("bm") == "2":
            sh += 0.9                                                                    # соседи плато s=3: 4 и 2 → Δ ≥ 0,5·Δ
        if k.get("s") == "2" and k.get("bm") == "2":
            sh += 0.9
        return sh
    w = World(eff)
    ev = ev_for(w)
    o = one(rf.run_procedure(ev, SPEC, "L", (0, 1, 2, 3, 4), w.ncoins, nboot=NB), ev)
    assert [s.axis for s in o.steps1 if s.accepted] == ["bm"]
    assert [s.axis for s in o.steps2 if s.accepted] == ["s"] and not o.converged and o.final == {}


def test_joint_grid_top_two_against_base():
    def eff(k, s, d):
        return {"4": 1.0, "3": 0.9, "6": 0.9}.get(k.get("s"), 0.0) + {"3": 1.0, "1": 0.8}.get(k.get("rr"), 0.0)
    w = World(eff)
    ev = ev_for(w)
    o = one(rf.run_procedure(ev, SPEC, "L", (0, 1, 2, 3, 4), w.ncoins, nboot=NB), ev)
    assert o.converged and set(o.cfg1) == {"s", "rr"}
    assert o.joint.axes == ["s", "rr"] and o.joint.n_cells == 4                         # 2 × 2
    assert o.final["s"]["id"] == "s_4" and o.final["rr"]["id"] == "rr_3"
    keys = [rf.cfg_keys(SPEC, "L", c["cfg"]) for c in o.joint.cells]
    assert len(set(keys)) == 4
    d = [c["d"] for c in o.joint.cells]
    assert d == sorted(d, reverse=True) and o.joint.cells[0]["passed"]


def test_no_acceptance_gives_base_and_delta_zero():
    w = World(lambda k, s, d: 0.0)
    ev = ev_for(w)
    o = one(rf.run_procedure(ev, SPEC, "L", (0,), w.ncoins, nboot=NB), ev)
    assert o.final == {} and o.converged and o.joint.chosen is None and o.m > 50


def test_features_group_with_provider():
    def eff(k, s, d):
        return 1.2 if k.get("fa") == "maxmv1" and k.get("fv") == repr(0.5) else 0.0
    w = World(eff)
    ev = ev_for(w)

    def provider(side, keys, om):
        yield from rf._need(ev, [])
        return {("maxmv1", "lo"): {10: 0.1, 20: 0.5, 25: 0.7}}
    om = rf.month_days(range(5))
    cfg, steps = one(rf.run_pass(ev, SPEC, "L", {}, om, w.ncoins, provider, nboot=NB), ev)
    f = [s for s in steps if s.group == 6]
    assert len(f) == 1 and f[0].accepted and f[0].winner == "F_maxmv1_lo_20" and "F_maxmv1_lo" in cfg
    assert "fa=maxmv1 fo=lo fv=0.5" in rf.cfg_keys(SPEC, "L", cfg)


def test_driver_lockstep_and_cache():
    eff = lambda k, s, d: 1.5 if k.get("rr") == "3" else 0.0              # noqa: E731
    w = World(eff)
    ev = ev_for(w)
    res = rf.drive(rf.procedure_gens(ev, SPEC, w.ncoins, nboot=NB, sides=("L",)), ev)
    assert set(res) == {"L:f1", "L:f2", "L:f3", "L:f4", "L:full"}
    uniq = len(ev.cache)
    assert w.calls == uniq                                                 # каждая конфигурация — один раз на все свёртки
    assert all(b > 0 for b in ev.batches)
    # пять процедур идут в ногу: раундов не больше, чем у одной процедуры + малый запас; число раундов << пять процедур подряд
    ev1 = ev_for(World(eff))
    rf.drive({"x": rf.run_procedure(ev1, SPEC, "L", (0, 1, 2, 3, 4), 12, nboot=NB)}, ev1)
    assert len(ev.batches) <= len(ev1.batches) + 2, (len(ev.batches), len(ev1.batches))
    assert all(res["L:f%d" % i].final["rr"]["id"] == "rr_3" for i in range(1, 5)) and res["L:full"].final["rr"]["id"] == "rr_3"


def test_walk_forward_confirmed_and_freeze():
    eff = lambda k, s, d: 1.5 if k.get("rr") == "3" else 0.0              # noqa: E731
    w = World(eff)
    ev = ev_for(w)
    res = rf.drive(rf.procedure_gens(ev, SPEC, w.ncoins, nboot=NB, sides=("L",)), ev)
    wf = rf.wf_stats(ev, SPEC, "L", res, NB)
    assert wf["n_days"] == 105 and wf["confirmed"] and wf["stat"].d > 1.0 and wf["stat"].lower > 0 and not wf["diverge"]
    assert wf["folds"] == [{"rr": "rr_3"}] * 4 and wf["full"] == {"rr": "rr_3"}
    assert rf.freeze_decision(wf)[0] == "C*" and "подтверждена" in rf.freeze_decision(wf)[1]
    own = rf.wf_own_verdict(ev, SPEC, "L", res, w.ncoins, NB)                                  # собственное среднее конфигураций свёрток на 105 сут.
    assert own["stat"].n == int(wf["N"].sum()) and own["stat"].mean > 1.0
    # нет эффекта: свёртки без шагов ⇒ Δ ≡ 0 ⇒ заморозка = база
    w0 = World(lambda k, s, d: 0.0)
    ev0 = ev_for(w0)
    r0 = rf.drive(rf.procedure_gens(ev0, SPEC, w0.ncoins, nboot=NB, sides=("S",)), ev0)
    wf0 = rf.wf_stats(ev0, SPEC, "S", r0, NB)
    assert wf0["stat"].d == 0.0 and not wf0["confirmed"] and rf.freeze_decision(wf0)[0] == "база"


def test_walk_forward_fold_slicing_uses_test_month_only():
    # эффект есть только в сентябре: свёртки 1–2 (обучение июль, июль–август) его не видят, свёртка 3 (обучение июль–сентябрь) — видит n=3 → нужно ≥ 2 месяцев с эффектом
    eff = lambda k, s, d: np.where(rf.MONTH_OF_DAY[d] == 2, 3.0, 0.0) if k.get("rr") == "3" else 0.0   # noqa: E731
    w = World(eff, noise=0.0)
    ev = ev_for(w)
    res = rf.drive(rf.procedure_gens(ev, SPEC, w.ncoins, nboot=NB, sides=("L",)), ev)
    assert all(res["L:f%d" % i].final == {} for i in range(1, 5))          # один месяц из n не проходит правило знака (n=3: ≥ 2; n=4: ≥ 3; n=5: ≥ 4)
    wf = rf.wf_stats(ev, SPEC, "L", res, NB)
    assert wf["stat"].d == 0.0 and rf.freeze_decision(wf)[0] == "база"


def test_choices_diverge():
    a = {"s": {"id": "s_3", "patch": {}}, "rr": {"id": "rr_3", "patch": {}}}
    b = {"s": {"id": "s_4", "patch": {}}}
    c = {"rr": {"id": "rr_3", "patch": {}}}
    assert rf.choices_diverge([a, c]) == {}
    assert rf.choices_diverge([a, b, c]) == {"s": ["s_3", "s_4"]}
    assert rf.choices_diverge([{}, a]) == {}                                # база не считается значением


# ---- собственный вердикт ------------------------------------------------------------------------------------------------------------

def _own(mean_shift, n=40, seed=5, spike=None):
    r = np.random.default_rng(seed)
    N = np.full(n, 4.0)
    x = r.normal(0.0, 1.0, n)
    S = (mean_shift + (x - x.mean())) * N                                  # среднее на сделку ровно mean_shift
    cS = np.array([S[i::8].sum() for i in range(8)])
    cN = np.array([N[i::8].sum() for i in range(8)])
    if spike is not None:
        S[:2] += spike
        cS[0] += spike * 2
    return S, N, cS, cN


def test_own_verdict_and_holm():
    S, N, cS, cN = _own(1.5)
    v = rf.own_verdict(S, N, cS, cN, nboot=NB)
    assert v["verdict"] == "подтверждено" and v["stat"].mean > 1 and v["stat"].p < 0.05
    S, N, cS, cN = _own(-1.5)
    assert rf.own_verdict(S, N, cS, cN, nboot=NB)["verdict"] == "опровергнуто"
    S, N, cS, cN = _own(0.02)
    assert rf.own_verdict(S, N, cS, cN, nboot=NB)["verdict"] == "не ясно"
    S, N, cS, cN = _own(0.0, spike=400.0)                                  # две лучших суток дают плюс — без них среднее ≤ 0
    S[2:] -= 0.5 * N[2:]
    v = rf.own_verdict(S, N, cS, cN, reject=True, nboot=NB)
    assert v["verdict"] != "подтверждено" and v["no_best_days"] <= 0
    assert rf.holm_reject([0.01, 0.03]) == [True, True] and rf.holm_reject([0.03, 0.04]) == [False, False] and rf.holm_reject([0.02, 0.2]) == [True, False]
    assert rf.side_off({"verdict": "не ясно"}, {"verdict": "опровергнуто"}) and not rf.side_off({"verdict": "подтверждено"}, {"verdict": "не ясно"})


# ---- признаки, ворота ----------------------------------------------------------------------------------------------------------------

def test_feature_cuts_training_only_and_availability():
    r = np.random.default_rng(2)
    vals = np.concatenate([r.normal(0, 1, 400), r.normal(10, 1, 400)])
    days = np.concatenate([np.full(400, 5), np.full(400, 50)])
    lo = rf.feature_cuts(vals, days, [5], "lo")
    assert set(lo) == {10, 20, 25} and lo[10] < lo[20] < lo[25] and lo[25] < 1.0                # только обучающие сутки (значения около 0)
    hi = rf.feature_cuts(vals, days, [5], "hi")
    assert hi[10] > hi[20] > hi[25] and hi[10] < 3.0
    assert np.isclose(lo[20], np.quantile(vals[:400], 0.2))
    v = np.array([1.0, np.nan, 2.0, 3.0] * 25)
    mo = np.repeat(np.arange(5), 20)
    ok, shares = rf.feature_availability(v, mo)
    assert not ok and all(abs(s - 0.75) < 1e-9 for s in shares)
    ok, _ = rf.feature_availability(np.ones(100), mo)
    assert ok
    v = np.ones(100)
    v[:2] = np.nan                                                              # 90 % в июле — снимается
    assert not rf.feature_availability(v, mo)[0]


# ---- выходы движка: приём, кэш .npz ------------------------------------------------------------------------------------------------------

def _write_out(outroot, req, entries_by_win):
    for w in rf.WINDOWS:
        d = os.path.join(outroot, w.name, req.vid)
        os.makedirs(d, exist_ok=True)
        rows = rows_for(w, entries_by_win.get(w.name, []))
        sd = rf.ENGINE_SIDE[req.side]
        with open(os.path.join(d, sd + "-trades.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("sym,level,sig_min,entry_min,exit_min,entry_px_e8,kind,takes,gross,fees,funding,net,entry_day,sig_ms,entry_ms,exit_ms\n")
            for r in rows:
                f.write("%s,0,0,0,0,0,%s,0,%.6f,%.6f,%.6f,%.6f,0,0,%d,%d\n" % (r["sym"], r["kind"], r["gross"], r["fees"], r["funding"], r["net"], r["entry_ms"], r["exit_ms"]))
        with open(os.path.join(d, sd + "-daily.csv"), "w", encoding="utf-8", newline="") as f:
            f.write("day,pnl\n")
            for i, v in sorted(daily_for(w, rows).items()):
                f.write("%d,%.6f\n" % (i, v))


def test_outdir_evaluator_ingest_and_npz_cache():
    ent = {"wa": [("AAA", "2026-07-03", 3.0, 0.5, 0.0, "stop")], "wb": [("AAA", "2026-09-05", 2.0, 0.5, -0.2, "tx")], "wc": [("AAA", "2026-11-01", 1.0, 0.5, 0.0, "rt")]}
    req = rf.Req("L", "rr=3", False)
    with tempfile.TemporaryDirectory() as tmp:
        cache = os.path.join(tmp, "cache")
        os.makedirs(cache)
        out = os.path.join(tmp, "out")
        seen = []

        def launch(reqs):
            seen.append([r.vid for r in reqs])
            for r in reqs:
                _write_out(out, r, ent)
        ev = rf.OutDirEvaluator(out, ["AAA"], {}, launch=launch, cache_dir=cache, run_t0={w.name: w.t0 for w in rf.WINDOWS})
        assert ev.variants_text([req]) == "%s rr=3\n" % req.vid
        ev.fulfil([req, req])
        s = ev.get(req)
        assert list(s.day) == [2, 66, 123] and np.allclose(s.val, [2.5, 1.5, 0.5]) and np.isclose(s.fund[1], -0.2)
        assert seen == [[req.vid]]
        ev2 = rf.OutDirEvaluator(out, ["AAA"], {}, launch=None, cache_dir=cache)
        assert ev2.missing([req]) == [] and np.allclose(ev2.get(req).val, s.val)                 # из .npz без запуска
        raises(rf.GuardError, rf.OutDirEvaluator, out, ["AAA"], None)
        ev3 = rf.OutDirEvaluator(out, ["AAA"], {}, launch=launch, run_t0={"wa": "2026-06-18", "wb": "2026-08-10", "wc": "2026-10-05"})
        assert "P42_T0" in raises(rf.GuardError, ev3.ingest, req)


def test_evaluator_missing_read_is_error():
    ev = rf.Evaluator(lambda r: None)
    assert "не посчитан" in raises(rf.GuardError, ev.get, rf.Req("L", "", False))


def main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("ok   ", n)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL ", n, "-", type(e).__name__, e)
            tb = traceback.extract_tb(e.__traceback__)
            print("       %s:%d  %s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].line))
    print("%d тестов, %d провалов" % (len(tests), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
