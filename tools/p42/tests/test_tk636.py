"""Тесты §13.35 (TK-636): окна внутри суток против перебора, причинность (мусор после t не меняет признаки), кластеры (посаженные группы, мусор после дня обновления), PEER (полуинтервал, различные монеты,
без ok_ev), 49 клеток на сторону (16 пробных / 32 соседа / 1 справочная), маски, композит COUP (≥ 3 из 4), исход стороны (работает / кандидат для вперёд / не работает / не ясно), совместный Холм 29 + m_new.
Синтетика, без движка, без P&L.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk636.py` — выполняет все test_* и печатает итог.
"""
import os
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
os.environ.setdefault("SIGFEAT_BASE_DATE", "2026-06-17")
os.environ.setdefault("SIGFEAT_DAYS", "80")
import tk604_stats as st  # noqa: E402
import tk636_feat as ft  # noqa: E402
import tk636_run as r6  # noqa: E402

NAN = float("nan")
RNG = np.random.default_rng(636)


def eq(a, b):
    return np.array_equal(np.asarray(a, dtype=float), np.asarray(b, dtype=float), equal_nan=True)


# ---- окна и компоненты -------------------------------------------------------------------------------------------------------------

def test_wstat_against_bruteforce():
    n = 400
    x, y = RNG.normal(0, 1, n), RNG.normal(0, 1, n)
    x = 0.6 * y + 0.8 * x
    x[RNG.integers(0, n, 30)] = NAN
    y[RNG.integers(0, n, 30)] = NAN
    for last, win, mn in ((399, 60, 40), (250, 15, 10), (120, 288, 100), (30, 60, 40), (399, 288, 100)):
        c, b = ft.wstat(x, y, last, win, mn)
        a = max(last - win + 1, 0)
        xs, ys = x[a:last + 1], y[a:last + 1]
        ok = np.isfinite(xs) & np.isfinite(ys)
        if ok.sum() < mn:
            assert c != c and b != b, (last, win)
            continue
        cc = np.corrcoef(xs[ok], ys[ok])[0, 1]
        bb = np.cov(xs[ok], ys[ok], ddof=0)[0, 1] / np.var(ys[ok])
        assert abs(c - cc) < 1e-12 and abs(b - bb) < 1e-12, (last, win, c, cc, b, bb)


def test_last_bar_closed_bars_only():
    # сигнал в минуте t: 1-мин бар t−1; 5-мин блок t // 5 − 1 — блок, содержащий t, не используется
    assert ft.last_bar("r1", 100) == 99
    assert ft.last_bar("r5", 100) == 19 and ft.last_bar("r5", 104) == 19 and ft.last_bar("r5", 105) == 20


def test_ret1_ret5_missing_minutes():
    o = np.array([1, 1, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1], dtype=np.int64)
    c = np.array([100, 101, 0, 103, 104, 105, 106, 107, 108, 109, 110, 111], dtype=np.int64)
    r = ft.ret1(o, c)
    assert np.isnan(r[0]) and abs(r[1] - 0.01) < 1e-12 and np.isnan(r[2]) and np.isnan(r[3]) and abs(r[4] - 104 / 103 + 1) < 1e-12
    r5 = ft.ret5(o, c)                                     # блоки 0–4, 5–9, (10–11 неполный блок отброшен)
    assert len(r5) == 2 and np.isnan(r5[0]) and abs(r5[1] - (109 / 104 - 1)) < 1e-12


def test_corr_features_causal_junk_after_t():
    n_coin, n = 8, 4000
    R1 = RNG.normal(0, 0.001, (n_coin, n))
    R1[1:] += 0.5 * R1[0]
    R5 = RNG.normal(0, 0.003, (n_coin, n // 5))
    R5[1:] += 0.5 * R5[0]
    t = 3000
    base = ft.corr_features(R1, R5, 3, 0, 1, np.array([2, 4, 5]), t, "XUSDT")
    J1, J5 = R1.copy(), R5.copy()
    J1[:, t:] = RNG.normal(0, 0.5, J1[:, t:].shape)
    J5[:, t // 5:] = RNG.normal(0, 0.5, J5[:, t // 5:].shape)
    junk = ft.corr_features(J1, J5, 3, 0, 1, np.array([2, 4, 5]), t, "XUSDT")
    assert base.keys() == junk.keys() and all(eq(base[k], junk[k]) for k in base), "мусор после t изменил признак"
    assert all(np.isfinite(base["cb_%s" % w]) for w in ("15m", "1h", "4h", "24h")), base
    # сам BTC / ETH: сравнение с самим собой пусто
    own = ft.corr_features(R1, R5, 0, 0, 1, np.array([2, 4, 5]), t, "BTCUSDT")
    assert all(np.isnan(own["%s_%s" % (c, w)]) for c in ("cb", "bt") for w in ft.WIN_ORDER) and np.isfinite(own["ce_1h"])
    # нет кластера (mates < 2) — cc пусто
    nocl = ft.corr_features(R1, R5, 3, 0, 1, np.zeros(1, dtype=np.int64), t, "XUSDT")
    assert all(np.isnan(nocl["cc_%s" % w]) for w in ft.WIN_ORDER)


def test_cc_excludes_self_and_needs_two_mates():
    n = 600
    R1 = RNG.normal(0, 0.001, (5, n))
    R5 = RNG.normal(0, 0.003, (5, n // 5))
    R1[4] = R1[2] + RNG.normal(0, 1e-4, n)
    t = 500
    f = ft.corr_features(R1, R5, 4, 0, 1, np.array([2, 3]), t, "X")
    assert f["cc_1h"] > 0.3                                  # сосед 2 тянет среднее
    # метка монеты в mates не должна влиять: среднее соседей без самой монеты — вызывающий код её не кладёт
    f2 = ft.corr_features(R1, R5, 4, 0, 1, np.array([3, 2]), t, "X")
    assert eq(f["cc_1h"], f2["cc_1h"])


# ---- кластеры ------------------------------------------------------------------------------------------------------------------------

def planted(n_per, groups, nb, seed=1):
    g = np.random.default_rng(seed)
    btc = g.normal(0, 0.003, nb)
    rows = [btc]
    lab = [-1]
    for k in range(groups):
        f = g.normal(0, 0.004, nb)
        for _ in range(n_per):
            rows.append(0.7 * btc + f + g.normal(0, 0.002, nb))
            lab.append(k)
    for _ in range(4):                                       # одиночки
        rows.append(0.7 * btc + g.normal(0, 0.004, nb))
        lab.append(-1)
    return np.vstack(rows), np.array(lab)


def test_clusters_recover_planted_groups_and_ignore_junk():
    R5, true = planted(5, 3, 4032 + 500)
    a, b = 0, 4032
    lab = ft.cluster_labels(R5, 0, None, a, b)
    for k in range(3):
        idx = np.flatnonzero(true == k)
        assert len(set(lab[idx])) == 1 and lab[idx[0]] >= 0, (k, lab[idx])
    assert len({lab[np.flatnonzero(true == k)[0]] for k in range(3)}) == 3
    assert lab[0] == -1 and (lab[true == -1] == -1).all()
    junk = R5.copy()
    junk[:, b:] = RNG.normal(0, 0.05, junk[:, b:].shape)
    assert np.array_equal(ft.cluster_labels(junk, 0, None, a, b), lab), "метки кластеров изменились от данных после дня обновления"


def test_refresh_blocks_14_days_before_first():
    import datetime
    a, b = ft.refresh_blocks(datetime.date(2026, 6, 17), datetime.date(2026, 7, 1))
    assert (a, b) == (0, 14 * 288)
    a, b = ft.refresh_blocks(datetime.date(2026, 7, 25), datetime.date(2026, 9, 1))
    assert b == 38 * 288 and b - a == 14 * 288
    assert ft.REFRESH_WINDOW[9] == "wb"                     # 18–31.08 не помещается в wa (данные до 29.08)


# ---- события и соседи ------------------------------------------------------------------------------------------------------------

def test_events_count_exact():
    m = ft.MIN_MS
    ev = ft.Events(["A", "A", "B", "C", "D"], [10 * m, 12 * m, 4 * m, 20 * m, 5 * m])
    t = 20 * m
    # [t − 15 мин, t) = [5, 20) мин: A (10, 12) — 1 монета; B (4) мимо; C (20) — не раньше t, мимо; D (5) — граница включена
    assert ev.count(["A", "B", "C", "D"], t, 15) == 2
    assert ev.count(["A", "B", "C", "D"], t, 5) == 0
    assert ev.count(["A", "B", "C", "D"], t + m, 5) == 1      # [16, 21): C (20)
    assert ev.count(["B"], t, 60) == 1 and ev.count([], t, 60) == 0
    assert ev.count_all("A", t, 15) == 1 and ev.count_all("Z", t, 15) == 2       # A: D (5); Z: A и D


def test_load_events_ignores_ok_ev():
    import pandas as pd
    tmp = tempfile.mkdtemp()
    pd.DataFrame(dict(side=["long", "long", "short", "long"], sym=["A", "B", "C", "D"], cross_ms=[100.0, 200.0, 300.0, NAN], ok_ev=[0, 1, 1, 1], sig_ms=[1, 2, 3, 4])).to_csv(os.path.join(tmp, "wa.csv"), index=False)
    old = dict(ft.EV_DIR)
    ft.EV_DIR["h775"] = tmp
    try:
        d = ft.load_events_df("h775", "wa", "L")
    finally:
        ft.EV_DIR.update(old)
    assert list(d["sym"]) == ["A", "B"] and list(d["cross_ms"]) == [100.0, 200.0], d      # ok_ev не фильтрует; пустой cross_ms и чужая сторона — нет
    ev = ft.events_of(d)
    assert ev.count(["A", "B"], 250, 1) == 2 and ev.count(["A"], 100, 1) == 0 and ev.count(["A"], 101, 1) == 1     # полуинтервал [t − W, t): cross_ms = t не считается


def test_peer_junk_after_t_unchanged():
    m = ft.MIN_MS
    rng = np.random.default_rng(5)
    syms = np.array(list("ABCDEFGH") * 10, dtype=object)
    cross = rng.integers(0, 600 * m, len(syms))
    t = 400 * m
    base = ft.Events(syms, cross)
    late = cross >= t
    cross2 = cross.copy()
    cross2[late] = t + rng.integers(0, 6 * 60 * m, int(late.sum()))
    junk = ft.Events(np.concatenate([syms, rng.choice(list("ABCDEFGH"), 20)]), np.concatenate([cross2, t + rng.integers(0, 60 * m, 20)]))
    for w in (5, 15, 60):
        assert base.count(list("BCDEFG"), t, w) == junk.count(list("BCDEFG"), t, w)
    assert base.count_all("A", t, 15) == junk.count_all("A", t, 15)


def test_age_days():
    import datetime
    ms = int(datetime.datetime(2026, 8, 10, 12, 0, tzinfo=datetime.timezone.utc).timestamp() * 1000)
    assert ft.age_days(ms, "2026-08-01") == 9.0 and ft.age_days(ms, "2026-08-20") == 0.0 and np.isnan(ft.age_days(ms, ""))


# ---- клетки --------------------------------------------------------------------------------------------------------------------------

def test_cell_specs_counts_and_order():
    sp = r6.cell_specs()
    kinds = [s["kind"] for s in sp]
    assert kinds.count("T") == 16 and kinds.count("N") == 32 and kinds.count("R") == 1 and len(sp) == 49
    assert kinds == sorted(kinds, key="TNR".index), "порядок: пробные, соседи, справочные"
    assert [s["cid"] for s in sp if s["kind"] == "T"][:8] == ["TOV_16", "TOV_24", "TOV_36", "TOV_54", "TOV_80", "AGE", "VOL", "PEER"]
    assert len({s["cid"] for s in sp}) == 49
    t = [s for s in sp if s["kind"] == "T"]
    assert len({s["group"] for s in t}) == 16, "у каждой пробной клетки своя группа соседей"
    assert all(sum(1 for s in sp if s["kind"] == "N" and s["group"] == c["group"]) == 2 for c in t)
    assert [s for s in sp if s["kind"] == "R"][0]["cid"] == "CLS"


def mk_d(n=1000, seed=3):
    g = np.random.default_rng(seed)
    d = {"turn24h": np.exp(g.normal(np.log(1e8), 1.2, n)), "age_d": g.integers(0, 2000, n).astype(float), "natr_bp": g.uniform(40, 400, n),
         "cls": (g.random(n) < 0.3).astype(float), "peer5": g.integers(0, 3, n).astype(float), "peer15": g.integers(0, 4, n).astype(float), "peer60": g.integers(0, 6, n).astype(float)}
    for w in ft.WIN_ORDER:
        for c in ft.COMP:
            d["%s_%s" % (c, w)] = g.normal(0, 1, n)
    d["age_d"][:20] = NAN
    d["peer15"][20:60] = NAN                                  # без кластера
    return d


def test_excl_masks_semantics():
    d = mk_d()
    refs = r6.make_refs(d)
    dd = r6.derive(d, refs)
    thr = r6.quantile_table(dd)
    by = {s["cid"]: s for s in r6.cell_specs()}
    m = r6.excl_mask(by["TOV_36"], dd, thr)
    assert eq(m, d["turn24h"] < 36e6), "TOV_X: исключается turn24h < X млн $ (торгуются только ≥ X)"
    assert eq(r6.excl_mask(by["TOV_36_up"], dd, thr), d["turn24h"] < 54e6) and eq(r6.excl_mask(by["TOV_36_dn"], dd, thr), d["turn24h"] < 24e6), "соседи — соседние ступени лестницы"
    assert r6.TOV_LADDER == (11, 16, 24, 36, 54, 80, 120) and by["TOV_16_dn"]["x"] == 11 and by["TOV_80_up"]["x"] == 120
    ma = r6.excl_mask(by["AGE"], dd, thr)
    assert not ma[:20].any(), "пусто проходит"
    assert abs(ma.mean() - 0.25) < 0.03
    mp = r6.excl_mask(by["PEER"], dd, thr)
    assert eq(mp, np.isfinite(d["peer15"]) & (d["peer15"] == 0)) and not mp[20:60].any(), "PEER: исключается только «кластер есть и 0»"
    assert eq(r6.excl_mask(by["PEER_w05"], dd, thr), np.isfinite(d["peer5"]) & (d["peer5"] == 0))
    assert eq(r6.excl_mask(by["CLS"], dd, thr), d["cls"] == 1)
    c_hi, c_lo = dd["COUP_15m"], dd["COUP_15m"]
    mh, ml = r6.excl_mask(by["COUP15m_hi"], dd, thr), r6.excl_mask(by["COUP15m_lo"], dd, thr)
    assert abs(mh.mean() - 0.25) < 0.03 and abs(ml.mean() - 0.25) < 0.03 and not (mh & ml).any()
    assert (c_hi[mh] > np.nanmedian(c_hi)).all() and (c_lo[ml] < np.nanmedian(c_lo)).all()
    assert r6.excl_mask(by["COUP15m_hi_q10"], dd, thr).sum() < mh.sum() < r6.excl_mask(by["COUP15m_hi_q40"], dd, thr).sum()
    assert r6.excl_mask(by["COUP15m_lo_q10"], dd, thr).sum() < ml.sum() < r6.excl_mask(by["COUP15m_lo_q40"], dd, thr).sum()


def test_composite_needs_three_of_four():
    refs = {"%s_15m" % c: np.sort(np.arange(100, dtype=float)) for c in ft.COMP}
    vals = {"%s_15m" % c: np.array([50.0, 50.0, 50.0]) for c in ft.COMP}
    vals["cb_15m"][1] = NAN
    vals["ce_15m"][2], vals["bt_15m"][2] = NAN, NAN
    out = r6.composite(vals, refs, "15m")
    assert np.isfinite(out[0]) and np.isfinite(out[1]) and np.isnan(out[2]), out
    assert abs(out[0] - 0.505) < 1e-9


def test_peer_bucket():
    x = np.array([0, 1, 2, 5, NAN])
    assert list(r6.peer_bucket(x, "cluster")) == [0, 1, 2, 2, 4]
    assert list(r6.peer_bucket(np.array([0, 1, 2, 3, 5, 6, 40]), "all")) == [0, 1, 1, 2, 2, 3, 3]


def test_cell_table_columns_ftab_style():
    tmp = tempfile.mkdtemp()
    n = r6.write_cell_table(os.path.join(tmp, "t.csv"), ["A", "B"], [10, 20], {"TOV_25": np.array([1.0, 0.0]), "CLS": np.array([0.0, 1.0])})
    head = open(os.path.join(tmp, "t.csv"), encoding="utf-8").read().splitlines()
    assert n == 2 and head[0] == "sym,sig_ms,TOV_25,CLS" and head[1] == "A,10,1.0,0.0"


# ---- исход стороны и Холм -------------------------------------------------------------------------------------------------------

def mk_ev(rows, passed):
    return dict(rows=rows, passed=passed)


def row(d, trial=True, admitted=True):
    return dict(d=d, trial=trial, admitted=admitted)


def test_side_outcome_rules():
    rows = {"L": {"TOV_100": row(1.0), "AGE": row(0.2), "CLS": row(5.0, trial=False)}, "S": {"TOV_100": row(-0.1), "AGE": row(-0.3), "VOL": row(-0.5, admitted=False)}}
    ev = mk_ev(rows, [("L", "TOV_100")])
    repl = {("L", "TOV_100"): dict(repl=True)}
    o, c = r6.side_outcome(ev, repl, "L")
    assert o.startswith("кандидат для вперёд") and c == ["TOV_100"], "прошла и повторилась только помеченная TOV → кандидат"
    ev2 = mk_ev(rows, [("L", "TOV_100"), ("L", "AGE")])
    o, c = r6.side_outcome(ev2, {("L", "TOV_100"): dict(repl=True), ("L", "AGE"): dict(repl=True)}, "L")
    assert o == "работает" and c == ["AGE"], "не помеченная прошла → работает"
    o, _ = r6.side_outcome(ev, {("L", "TOV_100"): dict(repl=False)}, "L")
    assert o == "не ясно", "прошла g1–g5, но не повторилась на o566 — не «работает» и не кандидат"
    o, _ = r6.side_outcome(ev, {}, "S")
    assert o == "не работает", "все допущенные пробные Δ < 0; CLS и недопущенные не считаются"
    rows3 = {"L": {"CLS": row(5.0, trial=False), "TOV_25": row(-1.0)}}
    assert r6.side_outcome(mk_ev(rows3, []), {}, "L")[0] == "не работает"
    assert r6.side_outcome(mk_ev({"L": {"CLS": row(5.0, trial=False)}}, []), {}, "L")[0] == "не ясно", "нет допущенных пробных клеток — «не ясно»"


def test_joint_holm_29_plus_new_and_side_context():
    trial = [("L", "A"), ("L", "B"), ("S", "C")]
    ps_new = [1e-6, 0.04, 1e-5]
    rows = {"L": {"A": dict(admitted=True, d=1.0, g2=True, g3=True, g4=True, g5=True), "B": dict(admitted=True, d=1.0, g2=True, g3=True, g4=True, g5=True)},
            "S": {"C": dict(admitted=True, d=1.0, g2=True, g3=True, g4=True, g5=False)}}
    ev = dict(rows=rows, holm=dict(m=3, ps=ps_new, trial=trial), passed=[])
    ps_old = [0.5] * 29
    out = r6.joint_holm(ev, ps_old)
    assert out["holm"]["m_joint"] == 32 and rows["L"]["A"]["g1"] and not rows["L"]["B"]["g1"]
    assert out["passed"] == [("L", "A")], "C не прошла g5"
    assert rows["L"]["A"]["g1_new_only"] and rows["L"]["A"]["g1_side"]
    assert len(r6.TOV_LEVELS) == 5 and r6.N_OLD_TRIAL == 29


def main():
    names = sorted(n for n in globals() if n.startswith("test_") and callable(globals()[n]))
    fail = 0
    for n in names:
        try:
            globals()[n]()
            print("ok  ", n)
        except Exception:
            fail += 1
            print("FAIL", n)
            traceback.print_exc()
    print("%d/%d" % (len(names) - fail, len(names)))
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
