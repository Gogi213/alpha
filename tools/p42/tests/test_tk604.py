"""Тесты §13.26 (TK-604): пробиваемость монеты — признак, таблицы, клетки, выбор п. 6, свёртки п. 7, изоляция. Синтетика, без движка, без P&L.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk604.py` — выполняет все test_* и печатает итог.
"""
import datetime
import os
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import tk604_feat as tf  # noqa: E402
import tk604_stats as st  # noqa: E402

NB = 1500                                       # повторов бутстрепа в тестах (в боевом запуске 20 000)
CM = tf.CLOSE_MS


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return str(e)
    raise AssertionError("ожидалось %s" % exc.__name__)


def ms(date, hh=0, mm=0):
    d = datetime.datetime.fromisoformat(date).replace(tzinfo=datetime.timezone.utc)
    return int(d.timestamp() * 1000) + (hh * 60 + mm) * 60_000


def eps(rows):
    """rows: (side, sym, abs_ms, y) → Episodes."""
    return tf.Episodes(np.array([r[0] for r in rows], dtype=object), np.array([r[1] for r in rows], dtype=object), np.array([r[2] for r in rows], dtype=np.int64),
                       np.array([r[3] for r in rows], dtype=np.int8), np.full(len(rows), -1, dtype=np.int8), np.full(len(rows), -1, dtype=np.int64),
                       np.ones(len(rows), dtype=bool))


# ---- признак: закрытие, причинность, K ----------------------------------------------------------------------------------------

def test_closure_boundary_equals():
    t0 = ms("2026-07-05")
    e = eps([("long", "AAA", t0, 1), ("long", "AAA", t0 + 10 * 60_000, 0)])
    h = tf.episode_history(e, "long")
    for t, want in ((t0 + CM - 1, 0), (t0 + CM, 1), (t0 + 10 * 60_000 + CM - 1, 1), (t0 + 10 * 60_000 + CM, 2)):
        n, k = h.counts(np.array(["AAA"], dtype=object), np.array([t]))
        assert int(n[0]) == want, (t - t0, n)
    assert CM == 65 * 60_000


def test_own_and_future_episodes_do_not_count():
    t = ms("2026-07-05", 12)
    e = eps([("long", "AAA", t, 1),                 # собственный (сигнал кандидата в тот же момент) — не закрыт
             ("long", "AAA", t + 3600_000, 1),      # будущее
             ("long", "AAA", t - CM, 1),            # закрылся ровно сейчас
             ("short", "AAA", t - 2 * CM, 1),       # другая сторона
             ("long", "BBB", t - 2 * CM, 1)])       # другая монета
    n, k, bk = tf.features(tf.episode_history(e, "long"), np.array(["AAA"], dtype=object), np.array([t]))
    assert (int(n[0]), int(k[0])) == (1, 1)


def test_bk_empty_below_K_and_ratio():
    t = ms("2026-08-01")
    rows = [("long", "AAA", t - (i + 2) * 3_600_000 * 2, 1 if i < 3 else 0) for i in range(25)]
    n, k, bk = tf.features(tf.episode_history(eps(rows), "long"), np.array(["AAA", "ZZZ"], dtype=object), np.array([t, t]))
    assert int(n[0]) == 25 and int(k[0]) == 3
    assert np.isnan(bk[40][0]) and np.isnan(bk[60][0]) and bk[20][0] == 3 / 25
    assert int(n[1]) == 0 and np.isnan(bk[20][1])                                # монеты нет в истории — пусто, не ошибка


def test_garbage_after_t_leaves_feature_unchanged():
    rng = np.random.default_rng(3)
    rows = [("long", "AAA", ms("2026-07-01") + int(x) * 60_000, int(rng.integers(0, 2))) for x in rng.integers(0, 60 * 1440, 400)]
    e = eps(rows)
    t = ms("2026-07-20")
    g = tf.garbage_after(e, t, 1)
    assert (g.abs_ms != e.abs_ms).any()
    a = tf.features(tf.episode_history(e, "long"), np.array(["AAA"], dtype=object), np.array([t]))
    b = tf.features(tf.episode_history(g, "long"), np.array(["AAA"], dtype=object), np.array([t]))
    assert int(a[0][0]) == int(b[0][0]) > 0 and int(a[1][0]) == int(b[1][0])
    # независимый перебор совпадает
    n, k = tf.brute_nk(e.sym, e.abs_ms, e.y, "AAA", t)
    assert (n, k) == (int(a[0][0]), int(a[1][0]))


def test_causality_check_passes_and_detects_leak():
    rng = np.random.default_rng(5)
    rows = []
    for sym in ("AAA", "BBB", "CCC"):
        for side in ("long", "short"):
            for x in rng.integers(0, 70 * 1440, 120):
                rows.append((side, sym, ms("2026-07-01") + int(x) * 60_000, int(rng.integers(0, 2))))
    e = eps(rows)
    cs = np.array(["long", "short"] * 30, dtype=object)
    csym = np.array(["AAA", "BBB", "CCC"] * 20, dtype=object)
    cabs = np.array([ms("2026-07-10") + int(x) * 60_000 for x in rng.integers(0, 40 * 1440, 60)], dtype=np.int64)
    picks = tf.pick_candidates(cs, cabs, [ms("2026-08-01")], total=50, per_seam_side=10)
    assert len(picks) >= 50
    assert tf.causality_check(e, cs, csym, cabs, picks) == []
    # утечка: история считает закрытыми и эпизоды ещё не закрывшиеся (сдвиг закрытия на −10 минут) → проверка обязана заметить
    orig = tf.episode_history
    try:
        tf.episode_history = lambda eps_, side: tf.History(eps_.sym[eps_.side == side], eps_.abs_ms[eps_.side == side] + CM - 30 * 24 * 3600_000, eps_.y[eps_.side == side].astype(float))
        assert tf.causality_check(e, cs, csym, cabs, picks) != []
    finally:
        tf.episode_history = orig


def test_seam_picks_cover_each_side():
    rng = np.random.default_rng(9)
    n = 3000
    side = np.array(["long", "short"] * (n // 2), dtype=object)
    abs_ms = np.array([ms("2026-06-17") + int(x) * 60_000 for x in rng.integers(0, 140 * 1440, n)], dtype=np.int64)
    seams = [ms("2026-07-01"), ms("2026-08-26"), ms("2026-10-03")]
    pick = tf.pick_candidates(side, abs_ms, seams, total=50, per_seam_side=10, half_days=3)
    for s in seams:
        for sd in ("long", "short"):
            c = [i for i in pick if side[i] == sd and abs(int(abs_ms[i]) - s) <= 3 * tf.DAY_MS]
            assert len(c) >= 10, (s, sd, len(c))


# ---- склейка эпизодов, изоляция -----------------------------------------------------------------------------------------------

def per_window_eps():
    out = {}
    for w in st.HIST_WINDOWS:
        # по одному эпизоду в каждые сутки окна ±3 суток за границы диапазона (они обязаны отброситься)
        rows = []
        d = w.lod - datetime.timedelta(days=3)
        while d <= min(w.hid + datetime.timedelta(days=3), st.LAST_DAY):
            rows.append(("long", "AAA", ms(d.isoformat(), 12), 1))
            d += datetime.timedelta(days=1)
        out[w.name] = eps(rows)
    return out


def test_splice_episodes_by_credit_ranges():
    pw = per_window_eps()
    e = tf.splice_episodes(pw)
    days = sorted({datetime.datetime.fromtimestamp(a / 1000, tz=datetime.timezone.utc).date() for a in e.abs_ms})
    assert days[0] == datetime.date(2026, 6, 17) and days[-1] == datetime.date(2026, 11, 13)
    assert len(days) == len(e) == (datetime.date(2026, 11, 13) - datetime.date(2026, 6, 17)).days + 1      # без наложений и пропусков
    assert (np.diff(np.sort(e.abs_ms)) > 0).all()


def test_window_rows_filter_label_and_indep():
    rows = [dict(side="long", sym="A", sig_ms="1000", indep="1", ok_t60="1", y="1", y_n="0", natr_e8="5", in_pool="1"),
            dict(side="long", sym="A", sig_ms="2000", indep="0", ok_t60="1", y="1", y_n="", natr_e8="5", in_pool="0"),       # не независимый
            dict(side="long", sym="A", sig_ms="3000", indep="1", ok_t60="0", y="", y_n="", natr_e8="5", in_pool="0"),        # нет метки
            dict(side="short", sym="B", sig_ms="4000", indep="1", ok_t60="1", y="0", y_n="", natr_e8="", in_pool="0")]
    e = tf.window_rows_to_arrays(rows, st.WA)
    assert list(e.sym) == ["A", "B"] and list(e.y) == [1, 0] and list(e.abs_ms) == [st.WA.t0_ms + 1000, st.WA.t0_ms + 4000]


def test_isolation():
    wd = st.Window("wd", "2026-11-03", "2026-11-17", "2026-11-30")
    assert "wd" in raises(st.GuardError, st.check_isolation, (wd,))
    late = st.Window("wc", "2026-09-01", "2026-10-03", "2026-11-14")
    raises(st.GuardError, st.check_isolation, (late,))
    raises(st.GuardError, st.check_dates, "2026-11-03", "2026-11-13")                      # T0 + 73 = 16.01 — позже 13.11
    raises(st.GuardError, st.check_dates, "2026-09-01", "2026-11-14")
    st.check_dates("2026-09-01", "2026-11-13")
    raises(st.GuardError, st.check_abs_ms, np.array([ms("2026-11-14")]))
    st.check_abs_ms(np.array([ms("2026-11-13", 23, 59)]))
    pw = per_window_eps()
    pw["wc"] = eps([("long", "AAA", ms("2026-11-14", 1), 1)])                               # эпизод 14.11 — отказ, не молчаливая отбраковка
    assert "позже 13.11" in raises(st.GuardError, tf.splice_episodes, pw)
    st.check_isolation(st.HIST_WINDOWS)
    st.check_windows()


def test_windows_cover_136_days():
    assert st.NDAYS == 136 and sum(st.MONTH_LEN) == 136
    assert (st.WC.hid - st.D0).days + 1 == 136
    bad = (st.WA, st.Window("wb", "2026-07-25", "2026-08-27", "2026-10-02"), st.WC)          # пропуск суток
    raises(st.GuardError, st.check_windows, bad)


# ---- таблица --ftab -----------------------------------------------------------------------------------------------------------

def test_ftab_roundtrip_and_blank_values():
    t = ms("2026-08-01")
    rows = [("long", "AAA", t - (i + 2) * 3_600_000 * 2, 1 if i < 2 else 0) for i in range(25)]
    h = tf.episode_history(eps(rows), "long")
    sym = np.array(["AAA", "BBB", "AAA"], dtype=object)
    sig = np.array([1000, 1000, 1000])                                                      # третья строка — дубль ключа (AAA, 1000)
    n, k, bk = tf.features(h, sym, np.array([t, t, t]))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.csv")
        assert tf.write_ftab(p, sym, sig, n, k, bk) == 2
        text = open(p, encoding="utf-8").read().splitlines()
        assert text[0] == "sym,sig_ms,n,k,bk20,bk40,bk60"
        assert text[1] == "AAA,1000,25,2,0.08,,", text[1]                                   # bk40/60 пусто при n < K
        assert text[2] == "BBB,1000,0,0,,,"
        s2, m2, n2, k2 = tf.read_ftab(p)
        assert list(s2) == ["AAA", "BBB"] and list(n2) == [25, 0]


def test_excluded_rule_ties_and_float():
    n = np.array([20, 20, 40, 60, 19])
    k = np.array([0, 1, 2, 3, 0])
    assert list(tf.excluded(n, k, 20, 0.05)) == [True, False, False, False, False]         # 1/20 = 0,05 не < 0,05; n = 19 < K
    assert list(tf.excluded(n, k, 20, 0.035)) == [True, False, False, False, False]
    assert list(tf.excluded(n, k, 40, 0.05)) == [False, False, False, False, False]        # 2/40 = 0,05 — не строго меньше
    assert list(tf.excluded(n, k, 20, 0.02)) == [True, False, False, False, False]
    n2, k2 = np.array([60, 60]), np.array([2, 3])
    assert list(tf.excluded(n2, k2, 60, 0.035)) == [True, False]                            # 2/60 = 0,0333 < 0,035; 3/60 = 0,05


def test_duplicate_cells_and_jaccard():
    n = np.full(50, 20)
    k = np.array([0] * 20 + [1] * 30)
    masks = tf.exclusion_masks(n, k)
    dup = tf.find_duplicates(masks)
    assert dup["K20_t020"] is None and dup["K20_t035"] == "K20_t020" and dup["K20_t050"] == "K20_t020"
    assert dup["K40_t020"] is None                                                          # n = 20 < 40 — пустое множество, не равно непустому
    assert dup["K40_t035"] == "K40_t020" and dup["K60_t050"] == "K40_t020"
    ids, m = tf.jaccard_matrix(masks)
    assert np.allclose(m, m.T) and m[0, 0] == 1.0 and m[0, 1] == 1.0 and m[0, 3] == 0.0   # пустое с непустым — 0
    # разные множества: n растёт — клетки различаются
    n = np.array([20, 30, 45, 61, 61, 61, 70])
    k = np.array([0, 0, 1, 1, 2, 3, 4])
    d2 = tf.find_duplicates(tf.exclusion_masks(n, k))
    assert sum(1 for v in d2.values() if v is None) < 9


# ---- G5: покрытие ---------------------------------------------------------------------------------------------------------------

def test_coverage_counts():
    nt = 600
    day = np.sort(np.random.default_rng(2).integers(0, 136, nt))
    n = np.where(day >= 40, 25, 5)
    k = np.where(day >= 40, np.where(np.arange(nt) % 5 == 0, 0, 3), 0)
    cand_abs = tf.D0_MS + day * tf.DAY_MS + 3600_000
    cov = tf.coverage(cand_abs, n, day, n, k)
    assert cov["N"] == nt
    assert cov["readable"][20] is True and cov["readable"][40] is False and cov["readable"][60] is False
    assert cov["by_K"][20]["first_cov_day"] == 40 and cov["by_K"][20]["covered_days"] == 96
    c = cov["cells"]["K20_t020"]
    assert c["N_cov"] == int((n >= 20).sum()) and c["E"] == int(((n >= 20) & (k == 0)).sum())
    assert abs(c["f_all"] - c["E"] / nt) < 1e-12 and abs(c["f_cov"] - c["E"] / c["N_cov"]) < 1e-12


def test_mde_matches_table2():
    d, m = st.mde_cell(5.7, 503, 0.3, 0.265)
    assert abs(d - 1.38) < 0.01 and abs(m - 0.37) < 0.01
    d, m = st.mde_cell(4.1, 311, 0.3, 0.199)
    assert abs(d - 1.26) < 0.02 and abs(m - 0.25) < 0.01
    d, m = st.mde_cell(5.7, 314, 0.3, 0.3, coef=2.80)
    assert abs(m - 0.59 * 2.80 / 2.80) < 0.2


# ---- AUC и ступень 1 -----------------------------------------------------------------------------------------------------------

def test_auc_ties_and_bruteforce():
    rng = np.random.default_rng(4)
    for _ in range(20):
        x = rng.integers(0, 6, 80).astype(float)
        y = (rng.random(80) < 0.3).astype(int)
        if y.sum() in (0, 80):
            continue
        pos, neg = x[y == 1], x[y == 0]
        want = sum((neg < a).sum() + 0.5 * (neg == a).sum() for a in pos) / (len(pos) * len(neg))
        assert abs(tf.auc(x, y) - want) < 1e-12
    x = np.array([1.0, np.nan, 3.0, 2.0])
    assert tf.auc(x, np.array([0, 1, 1, 0])) == 1.0                                        # NaN исключён: остаются (1,0), (3,1), (2,0)
    assert tf.auc(np.array([1.0, 1.0, 2.0, 2.0]), np.array([0, 1, 0, 1])) == 0.5
    assert np.isnan(tf.auc(np.array([1.0, 2.0]), np.array([1, 1])))


def test_wilson_and_strata():
    lo, hi = tf.wilson(5, 100)
    assert 0.02 < lo < 0.05 < hi < 0.12
    bk = np.array([0.0] * 50 + list(np.linspace(0.01, 0.2, 40)) + [0.05] * 10)
    n = np.array([20] * 90 + [5] * 10)
    spec = tf.strata_edges(bk, n)
    assert spec["zero_alone"] is True and len(spec["edges"]) == 2
    lab = tf.assign_strata(bk, n, spec)
    assert (lab[90:] == "мало истории").all() and (lab[:50] == "bk=0").all() and set(lab[50:90]) == {"T1", "T2", "T3"}
    tl = tf.trade_strata(np.array([np.nan, 0.0, 0.01, 0.9]), spec)
    assert list(tl[:2]) == ["нет значения", "bk20=0"]


def test_auc_ci_runs():
    rng = np.random.default_rng(8)
    n = 600
    x = rng.random(n)
    y = (rng.random(n) < 0.1 + 0.2 * x).astype(int)
    days = rng.integers(0, 60, n)
    coins = rng.integers(0, 20, n)
    r = tf.auc_ci(x, y, days, coins, nboot=100)
    assert r["auc"] > 0.5 and r["lo_d"] < r["auc"] < r["hi_d"] and 0 < r["p"] <= 1


# ---- ряды, Δ, склейка сделок ---------------------------------------------------------------------------------------------------

def rows_for(win, n_per_day=3, seed=1, days=None, t0=None):
    """Строки <side>-trades.csv окна: по n_per_day сделок в сутки по всему зачётному диапазону (+ сутки разгона и хвоста вне диапазона)."""
    rng = np.random.default_rng(seed)
    out = []
    d0 = (win.lod - win.t0d).days - 2
    d1 = (win.hid - win.t0d).days + 2
    for d in range(d0, d1 + 1):
        for j in range(n_per_day):
            gross = float(rng.normal(0.3, 3))
            fees = 0.2
            e = d * st.DAY_MS + 3600_000 * (j + 1)
            out.append(dict(sym="S%d" % (j % 4), sig_ms=str(e - 5000), entry_ms=str(e), exit_ms=str(e + 1800_000), gross="%.6f" % gross, fees="%.6f" % fees,
                            funding="0", net="%.6f" % (gross - fees), kind="take"))
    return out


def tables():
    return {w.name: st.table_from_rows(rows_for(w, seed=i)) for i, w in enumerate(st.TRADE_WINDOWS)}


def test_splice_ranges_sums_and_keys():
    tb = tables()
    ser = st.splice(tb, ["S0", "S1", "S2", "S3"])
    assert len(ser) == 136 * 3 and ser.day.min() == 0 and ser.day.max() == 135
    assert (np.bincount(ser.day) == 3).all()                                                # без наложений и пропусков
    # ключ сигнала — абсолютное время (T0 окна + sig_ms)
    w = st.WB
    i = int(np.nonzero(ser.day == (w.lod - st.D0).days)[0][0])
    assert ser.key_ms[i] >= w.t0_ms and ser.t_out[i] > ser.key_ms[i]
    # правка net в строке — охрана
    tb2 = tables()
    tb2["wa"].net[5] += 1.0
    raises(st.GuardError, st.splice, tb2, ["S0", "S1", "S2", "S3"])
    # монета вне таблицы
    raises(st.GuardError, st.splice, tables(), ["S0", "S1"])
    # daily: Σ среза = Σ net среза
    daily = {}
    for w in st.TRADE_WINDOWS:
        t = tb[w.name]
        d = st.abs_day(w, t.entry_ms)
        rows = {}
        for i in range(len(t)):
            g = int(t.entry_ms[i] // st.DAY_MS) - st.GRID_START_DAY
            rows[g] = rows.get(g, 0.0) + float(t.net[i])
        daily[w.name] = rows
    st.splice(tb, ["S0", "S1", "S2", "S3"], daily=daily)
    daily["wb"][(st.WB.lod - st.WB.t0d).days - st.GRID_START_DAY] += 0.5
    raises(st.GuardError, st.splice, tb, ["S0", "S1", "S2", "S3"], daily=daily)


def test_g2_tables_equal():
    t = st.table_from_rows(rows_for(st.WA, seed=3))
    t2 = st.table_from_rows(rows_for(st.WA, seed=3))
    ok, n, diff = st.tables_equal(t, t2, st.WA)
    assert ok and n > 100 and diff == 0
    t2.gross[10] += 0.01
    assert st.tables_equal(t, t2, st.WA)[0] is False


def test_delta_stat_deterministic_and_p():
    rng = np.random.default_rng(1)
    Nr = np.full(136, 5.0)
    Sr = rng.normal(0, 3, 136)
    Sc, Nc = Sr * 0.8 + 6.0, np.full(136, 4.0)
    a = st.delta_stat(Sc, Nc, Sr, Nr, NB)
    b = st.delta_stat(Sc, Nc, Sr, Nr, NB)
    assert a == b and a.p < 0.01 and a.z > 2 and a.d > 0
    same = st.delta_stat(Sr, Nr, Sr, Nr, NB)                                                # клетка ≡ база: Δ = 0, p = 1
    assert same.d == 0 and same.p == 1.0 and same.z == 0.0
    raises(st.GuardError, st.delta_stat, Sc, np.zeros(136), Sr, Nr, NB)


def test_holm_two_sides():
    assert st.holm_reject([0.02, 0.04]) == [True, True]
    assert st.holm_reject([0.03, 0.04]) == [False, False]
    assert st.holm_reject([0.01, 0.2]) == [True, False]
    assert st.holm_reject([1.0, 1.0]) == [False, False]


def test_greedy_and_mirror():
    S = np.array([10.0, 1.0, 1.0, 1.0, 1.0])
    N = np.ones(5)
    z, zn = np.zeros(5), np.ones(5)
    assert abs(st._greedy(S, N, z, zn) - 1.0) < 1e-12                                       # сняты сутки с 10 и одни из единичных: остаются 1, 1, 1
    assert st._greedy(S[:2], N[:2], z[:2], zn[:2]) is None                                  # единиц < k + 1
    # зеркало «не отделяет»: Δ < 0 держится без двух худших суток / монет
    nd = st.NDAYS
    day = np.arange(nd)
    r = st.make_series(day, day % 10, np.zeros(nd))
    c = st.make_series(day, day % 10, np.full(nd, -1.0))
    ok, dd, dc = st.robust_negative(c, r, np.arange(nd), 10)
    assert ok and abs(dd + 1.0) < 1e-12 and abs(dc + 1.0) < 1e-12
    v = np.full(nd, 0.1)
    v[0] = -200.0
    c2 = st.make_series(day, day % 10, v)
    ok2, dd2, dc2 = st.robust_negative(c2, r, np.arange(nd), 10)
    assert not ok2 and dd2 > 0                                                              # один выброс — не устойчиво
    okp, ddp, dcp = st.robust(c2, r, np.arange(nd), 10)
    assert not okp


# ---- выбор п. 6 на синтетическом мире ----------------------------------------------------------------------------------------

THR = {(i, j): 0.01 + 0.05 * i + 0.04 * j for i in range(3) for j in range(3)}


def make_world(effect, seed=11, per_day=5, ncoins=30, flip_after=None, readable=(True, True, True), thr=THR):
    """Мир: 136 суток, per_day сделок базы в сутки; у каждой «плохость» b ∈ [0, 1). Клетка (i, j) исключает b < thr(i, j). Сделка плохая (b < 0,25) —
    в среднем −3·effect, прочие +1·|effect| (effect < 0 — наоборот); при `flip_after` (индекс месяца) знак эффекта в месяцах ≥ flip_after меняется. Серия клетки = база без исключённых."""
    rng = np.random.default_rng(seed)
    day = np.repeat(np.arange(st.NDAYS), per_day)
    n = len(day)
    sym = rng.integers(0, ncoins, n)
    b = rng.random(n)
    eff = np.full(n, float(effect))
    if flip_after is not None:
        eff[st.MONTH_OF_DAY[day] >= flip_after] *= -1
    val = rng.normal(0, 3, n) + np.where(b < 0.25, -3.0 * eff, 1.0 * eff)
    base = st.make_series(day, sym, val, np.arange(n) * 1000 + st.D0.toordinal() * 0)
    cells = []
    for K, t in st.GRID:
        i, j = st.GRID_K.index(K), st.GRID_TAU.index(t)
        ex = b < thr[(i, j)]
        keep = ~ex
        cells.append(st.CellRun(st.cell_id(K, t), K, t, st.make_series(day[keep], sym[keep], val[keep], base.key_ms[keep]), ex))
    return st.SideData(base, cells, {20: readable[0], 40: readable[1], 60: readable[2]}, ncoins)


def test_select_accepts_clear_effect():
    side = make_world(1.0)
    sel = st.select_cell(side, np.arange(st.NDAYS), NB)
    assert sel.winner == "K60_t050" and sel.accepted, (sel.winner, sel.reasons)
    assert all(v[0] for v in sel.detail.values()), sel.detail
    assert sel.stat.z > 2 and sel.stat.d > 0.3


def test_select_rejects_no_effect():
    side = make_world(0.0, seed=21)
    sel = st.select_cell(side, np.arange(st.NDAYS), NB)
    assert not sel.accepted and (sel.winner is None or sel.reasons)
    assert st.chosen(sel) is None


def test_select_unreadable_K_not_admitted():
    side = make_world(1.0, readable=(True, True, False))
    sel = st.select_cell(side, np.arange(st.NDAYS), NB)
    assert sel.winner != "K60_t050" and not any(r["admitted"] for r in sel.rows if r["K"] == 60)
    side = make_world(1.0, readable=(False, False, False))
    sel = st.select_cell(side, np.arange(st.NDAYS), NB)
    assert sel.winner is None and not sel.accepted


def test_select_min_excluded_a2_and_min_trades_a1():
    side = make_world(1.0, per_day=1)                                                      # 136 сделок: исключено < 30 у всех клеток (макс 0,19·136 = 26) и месяц ноября < 8
    sel = st.select_cell(side, np.arange(st.NDAYS), NB)
    assert sel.winner is None and all(not r["a2"] for r in sel.rows)
    side = make_world(1.0, per_day=3)                                                      # сделок в месяце 39 / 13·3 = 39 — (а1) проходит у месяцев, (а2) у части клеток
    sel = st.select_cell(side, np.arange(st.NDAYS), NB)
    assert any(r["a2"] for r in sel.rows) and any(not r["a2"] for r in sel.rows)


def test_select_plateau_needs_independent_neighbours():
    # соседи победителя почти то же множество (Jaccard > 0,8) → плато не зачтено
    thr = dict(THR)
    thr[(2, 2)] = 0.19
    thr[(1, 2)] = 0.17                                                                      # J = 0,895 > 0,8
    thr[(2, 1)] = 0.16                                                                      # J = 0,842 > 0,8
    side = make_world(1.0, thr=thr)
    sel = st.select_cell(side, np.arange(st.NDAYS), NB)
    assert sel.winner in ("K60_t050",) and not sel.accepted and not sel.detail["g_plateau"][0], sel.detail["g_plateau"]


def test_select_months_need_two_informative():
    # эффект только в одном месяце (июль): у остальных месяцев исключаемые сделки не «плохие» → Δ по месяцам разного знака / информативных мало
    side = make_world(1.0, flip_after=1)
    sel = st.select_cell(side, np.arange(st.NDAYS), NB)
    assert not sel.accepted
    # m < 2: обучение на июле → всегда «нет фильтра»
    side = make_world(1.0, per_day=7)
    sel = st.select_cell(side, st.month_days((0,)), NB)
    assert not sel.accepted and "c_months" in sel.detail and not sel.detail["c_months"][0], (sel.winner, sel.reasons)


def test_duplicate_cells_are_not_neighbours_nor_runs():
    side = make_world(1.0)
    side.cells[-2] = st.CellRun(side.cells[-2].cid, side.cells[-2].K, side.cells[-2].tau, None, side.cells[-2].excl, dup_of="K60_t020")
    assert side.cells[-2].cid not in [c.cid for c in st.lattice_neighbours("K60_t050", side.cells)]
    sel = st.select_cell(side, np.arange(st.NDAYS), NB)
    assert all(r["cid"] != side.cells[-2].cid for r in sel.rows)


# ---- проверка вперёд п. 7 ----------------------------------------------------------------------------------------------------

def test_walk_forward_confirmed_on_clear_effect():
    side = make_world(1.0, per_day=6, seed=31)
    wf = st.walk_forward(side, NB)
    assert wf["n_days"] == 105 and len(wf["folds"]) == 4
    assert wf["folds"][0]["cid"] is None                                                   # июль: m < 2 → «нет фильтра»
    assert all(f["cid"] == "K60_t050" for f in wf["folds"][1:]), [f["cid"] for f in wf["folds"]]
    assert wf["active_days"] == 31 + 30 + 31 + 13 - 31 and not wf["none"]                  # свёртки 2–4: 74 суток с активным фильтром
    assert wf["stat"].d > 0 and wf["guard"] and wf["robust"]
    side_s = make_world(0.0, per_day=6, seed=32)
    wf2 = st.walk_forward(side_s, NB)
    v = st.verdicts({"L": wf, "S": wf2})
    assert v["L"] == ("подтверждено", True), v
    assert v["S"][1] is False and v["S"][0] != "подтверждено"


def test_walk_forward_none_when_procedure_selects_nothing():
    side = make_world(1.0, per_day=1, seed=33)                                              # (а2) нигде не проходит → «нет фильтра» в каждой свёртке
    wf = st.walk_forward(side, NB)
    assert wf["none"] and wf["stat"].d == 0 and wf["p"] == 1.0 and wf["active_days"] == 0
    v = st.verdicts({"L": wf, "S": wf})
    assert v["L"][0] == "не ясно — процедура не выбрала фильтр" and v["L"][1] is False


def test_walk_forward_does_not_separate_when_effect_flips():
    side = make_world(1.0, per_day=6, seed=34, flip_after=2)                               # июль–август: исключаемое плохо; с сентября — хорошо
    wf = st.walk_forward(side, NB)
    chosen = [f["cid"] for f in wf["folds"]]
    assert chosen[1] is not None and not wf["none"], chosen
    assert wf["stat"].d < 0 and wf["neg_robust"], (wf["stat"].d, wf["no_worst_days"], wf["no_worst_coins"])
    v = st.verdicts({"L": wf, "S": wf})
    assert v["L"][0] == "не отделяет" and v["L"][1] is False


def test_test_series_is_base_when_fold_has_no_filter():
    side = make_world(1.0, per_day=6, seed=35)
    wf = st.walk_forward(side, NB)
    # июль-свёртка выбрала «нет фильтра»: в первых 31 сутках склейки (август) клетка = база → Δ ≡ 0 только если фильтр не активен; активные сутки — 74
    n_aug = 31
    assert wf["folds"][0]["active_days"] == 0
    assert np.allclose(wf["S"][:n_aug], wf["Sr"][:n_aug]) and np.allclose(wf["N"][:n_aug], wf["Nr"][:n_aug])


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
