#!/usr/bin/env python3
"""TK-493, П-38 G-C2-2, G-C2-3, G-C2-4, G-C2-5, G-C2-9: тесты ядра C2 с ИЗВЕСТНЫМ ответом на синтетике. Запуск на Deck:
    cd ~/xdl9/audit3/conv/code && ~/xdl9/venv/bin/python test_conv.py [имя_теста ...]
Данные реальных панелей не читаются (только синтетика и временный каталог). Ответы считаются в тесте формулами, а не копируются из кода."""
import json
import math
import os
import subprocess
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_guard as G            # noqa: E402
import conv_lib as L              # noqa: E402
import conv_naive as NV           # noqa: E402

BASE = 100.0
N0 = 1500
CFG = dict(L=300, nb=60)          # короткие окна только для синтетики (боевые: L=1440, nb=240)


def chain_open(c):
    o = c.copy()
    for t in range(1, len(c)):
        o[t] = c[t - 1] if (math.isfinite(c[t - 1]) and math.isfinite(c[t])) else c[t]
    return o


def make_pair(N=N0, lvlA=None, lvlB=None, gapA=(), gapB=(), cp_kw=None, cfg_kw=None, key=("k1",), legs=None, c=None, floor=1.0, funA=None, funB=None,
              sess=None, m0=0, noise=None):
    cfg = L.Cfg(**dict(CFG, **(cfg_kw or {})))
    cp = L.ClassParams(**dict(dict(m_r=5, kstale=30), **(cp_kw or {})))
    lA = np.zeros(N) if lvlA is None else np.asarray(lvlA, float)
    lB = np.zeros(N) if lvlB is None else np.asarray(lvlB, float)
    cA = BASE * np.exp(lA / 1e4)
    cB = BASE * np.exp(lB / 1e4)
    if noise is not None:
        cA = cA * np.exp(noise[0] / 1e4)
        cB = cB * np.exp(noise[1] / 1e4)
    for a, b in gapA:
        cA[a:b] = np.nan
    for a, b in gapB:
        cB[a:b] = np.nan
    if sess is not None:
        cA = np.where(sess, cA, np.nan)
        cB = np.where(sess, cB, np.nan)
    oA, oB = chain_open(cA), chain_open(cB)
    frA = frB = None
    if funA:
        frA = np.zeros(N)
        for m, r in funA.items():
            frA[m] = r
    if funB:
        frB = np.zeros(N)
        for m, r in funB.items():
            frB[m] = r
    ps = L.PairSeries(oA, cA, oB, cB, m0, cfg, key=key, legs=legs, floor=floor, c=c, sess=sess, frA=frA, frB=frB, semA="chain", semB="chain")
    return ps, cp, cfg


def run1(ps, cp, cfg, combo, period0=100):
    ft = L.Feats(ps, cp, cfg, period0)
    return L.simulate_component([ps], cp, cfg, combo, [ft], period0)[0]


def lvl(N, *segs):
    a = np.zeros(N)
    for s, e, v in segs:
        a[s:e] = v
    return a


def tr(sim, i=0):
    return dict(zip(L.TRADE_FIELDS, sim.trades[i]))


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


COMBO = L.Combo(25.2, 3, 1, 1440, 2.0)
OK = []


def test(fn):
    OK.append(fn)
    return fn


# ------------------------------------------------------------------ G-C2-3
@test
def t01_constant_spread_no_trades():
    rng = np.random.default_rng(1)
    ps, cp, cfg = make_pair(noise=(rng.normal(0, 0.2, N0), rng.normal(0, 0.2, N0)))
    sm = run1(ps, cp, cfg, COMBO)
    assert len(sm.trades) == 0 and sm.cnt["signal_total"] == 0, (len(sm.trades), dict(sm.cnt))


@test
def t02_single_spike_known_gross_net():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 705, 100.0)))
    sm = run1(ps, cp, cfg, COMBO)
    assert len(sm.trades) == 1, len(sm.trades)
    t = tr(sm)
    want_gross = 1e4 * (0.0 - (math.exp(-0.01) - 1.0))
    assert t["t_sig"] == 700 and t["f_in"] == 702 and t["u_dec"] == 705 and t["f_out"] == 707, t
    assert t["dir"] == 1.0 and L.CODES[int(t["code"])] == "CV", t
    assert close(t["gross"], want_gross, 1e-6) and close(t["net"], want_gross - 12.6, 1e-6), (t["gross"], want_gross)
    assert t["hold"] == 5 and t["mtm"] == 0 and t["deferred"] == 0 and t["ffill"] == 0


@test
def t03_spike_shorter_than_two_minutes_plus1_is_zero_gross():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 701, 100.0)))
    sm = run1(ps, cp, cfg, COMBO)
    assert len(sm.trades) == 1
    t = tr(sm)
    assert close(t["gross"], 0.0, 1e-9) and close(t["net"], -12.6, 1e-9), t
    # delay-0 видит «красивую» цену последней сделки сигнальной минуты (цепочка open[t+1] = close[t])
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 701, 100.0)), cfg_kw=dict(delay=0))
    t0 = tr(run1(ps, cp, cfg, COMBO))
    want = 1e4 * (0.0 - (math.exp(-0.01) - 1.0))
    assert close(t0["gross"], want, 1e-6) and t0["f_in"] == 701, t0


@test
def t04_timeout():
    T = 120
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 823, 100.0), (823, N0, 110.0)))
    sm = run1(ps, cp, cfg, L.Combo(25.2, 3, 1, T, None))
    assert len(sm.trades) == 1
    t = tr(sm)
    want = 1e4 * (0.0 - (math.exp(0.001) - 1.0))
    assert L.CODES[int(t["code"])] == "TO" and t["u_dec"] == 702 + T and t["f_out"] == 702 + T + 2, t
    assert close(t["gross"], want, 1e-6) and t["hold"] == T + 2, (t["gross"], want)


@test
def t05_mtm_open_at_end():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 1400, 100.0), (1400, N0, 105.0)))
    sm = run1(ps, cp, cfg, L.Combo(25.2, 3, 1, 100000, None))
    assert len(sm.trades) == 1 and sm.cnt["open_at_end"] == 1
    t = tr(sm)
    want = 1e4 * (0.0 - (math.exp(0.0005) - 1.0))
    assert t["mtm"] == 1 and close(t["gross"], want, 1e-6) and close(t["net"], want - 12.6, 1e-6), t


@test
def t06_two_overlapping_spikes_one_position():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 703, 100.0), (703, 709, 150.0)))
    sm = run1(ps, cp, cfg, COMBO)
    assert len(sm.trades) == 1 and sm.cnt["signal_total"] == 1, (len(sm.trades), dict(sm.cnt))
    t = tr(sm)
    assert t["u_dec"] == 709 and t["f_out"] == 711 and L.CODES[int(t["code"])] == "CV", t


@test
def t07_stop_pause_reentry_exclusion():
    N = 2600
    pat = lambda s: [(s, s + 3, 100.0), (s + 3, s + 12, 250.0)]
    segs = pat(700) + pat(800) + pat(1100) + pat(1600) + pat(2100)
    T = 360
    ps, cp, cfg = make_pair(N=N, lvlA=lvl(N, *segs))
    sm = run1(ps, cp, cfg, L.Combo(25.2, 3, 1, T, 2.0))
    codes = [L.CODES[int(tr(sm, i)["code"])] for i in range(len(sm.trades))]
    assert codes == ["S0", "S0", "S0"], codes
    t0 = tr(sm, 0)
    want = -1e4 * (math.exp(0.015) - 1.0)
    assert t0["u_dec"] == 703 and t0["f_out"] == 705 and close(t0["gross"], want, 1e-6), t0
    assert [tr(sm, i)["t_sig"] for i in range(3)] == [700, 1100, 1600], [tr(sm, i)["t_sig"] for i in range(3)]   # 800 - внутри паузы
    assert sm.excluded and sm.cnt["pair_excluded"] == 1 and sm.n_stops == 3     # четвёртый всплеск (2100) без сделки


@test
def t08_deferred_entry_and_signal_expired():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 710, 100.0)), gapB=[(701, 704)])
    sm = run1(ps, cp, cfg, COMBO)
    assert len(sm.trades) == 1 and sm.cnt["entry_deferred"] == 1
    t = tr(sm)
    assert t["f_in"] == 705 and t["deferred"] == 1, t
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 710, 100.0)), gapB=[(701, 704)], cp_kw=dict(kstale=3))
    sm = run1(ps, cp, cfg, COMBO)
    assert len(sm.trades) == 0 and sm.cnt["signal_expired"] == 1 and sm.rest_from == 700 + 3 + 1, (dict(sm.cnt), sm.rest_from)


@test
def t09_exit_at_last_known_price():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 710, 100.0)), gapB=[(711, 717)])
    sm = run1(ps, cp, cfg, COMBO)
    t = tr(sm)
    assert t["ffill"] == 1 and sm.cnt["exit_ffill"] == 1 and t["u_dec"] == 710 and t["f_out"] == 712, t
    assert close(t["gross"], 1e4 * (0.0 - (math.exp(-0.01) - 1.0)), 1e-6)       # нога B - последний close (100), нога A - open[712] = 100


@test
def t10_confirmation_p3():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 702, 100.0), (900, 903, 100.0)))
    sm = run1(ps, cp, cfg, L.Combo(25.2, 3, 3, 1440, 2.0))
    assert len(sm.trades) == 1 and tr(sm)["t_sig"] == 902, [tr(sm, i)["t_sig"] for i in range(len(sm.trades))]
    sm1 = run1(ps, cp, cfg, L.Combo(25.2, 3, 1, 1440, 2.0))
    assert [tr(sm1, i)["t_sig"] for i in range(len(sm1.trades))] == [700, 900]


@test
def t11_ceiling_gmax_and_s1():
    edges, vals = np.array([25.2, 40.0, 80.0, 160.0]), np.array([5.0, 5.0, 5.0, 5.0])
    kw = dict(gmax=80.0, a90_edges=edges, a90_vals=vals)
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 705, 100.0)), cp_kw=kw)
    sm = run1(ps, cp, cfg, COMBO)
    assert len(sm.trades) == 0 and sm.f.cand(25.2, 3, 1)[2]["drop_range"] > 0       # выше потолка - вход запрещён, не убирается из счётчика
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 703, 60.0), (703, 712, 85.0)), cp_kw=kw)
    sm = run1(ps, cp, cfg, COMBO)
    t = tr(sm)
    assert L.CODES[int(t["code"])] == "S1" and t["u_dec"] == 703, t
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 703, 60.0), (703, 712, 85.0)), cp_kw=kw, cfg_kw=dict(ceiling=False))
    sm = run1(ps, cp, cfg, COMBO)
    assert L.CODES[int(tr(sm)["code"])] == "CV" and len(sm.trades) == 1       # без потолка: ни запрета, ни S1


@test
def t12_stale_leg_s2_stop():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 760, 100.0)), gapB=[(710, 790)])
    sm = run1(ps, cp, cfg, COMBO)
    t = tr(sm)
    assert L.CODES[int(t["code"])] == "S2" and t["u_dec"] == 710 + 30 - 1 and t["ffill"] == 1 and sm.n_stops == 1, t


@test
def t13_cell_common_factor_and_s3():
    rng = np.random.default_rng(5)
    P, N = 6, 400
    base = rng.normal(0, 3, N)
    D = np.stack([base + rng.normal(0, 0.5, N) for _ in range(P)])
    D[2, :50] = np.nan
    c, cnt = L.cell_common(D, 5)
    for t in (10, 100, 300):
        v = D[:, t]
        v = v[np.isfinite(v)]
        assert close(c[t], float(np.median(v)), 1e-12)
    assert (cnt[:50] == 5).all() and c[0] != 0.0
    D2 = D.copy()
    D2[:4, :] = np.nan
    c2, _ = L.cell_common(D2, 5)
    assert (c2 == 0.0).all()                                      # меньше 5 пар - c = 0 (cell_thin)
    # общий сдвиг 100 bps во всей ячейке не даёт сигнала: g = |s - b - c| ~ 0
    cc = np.zeros(N0)
    cc[700:720] = 100.0
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 720, 100.0)), c=cc)
    assert len(run1(ps, cp, cfg, COMBO).trades) == 0
    # S3: в позиции сдвиг c на 40 > q_c = 30 - стоп
    c3 = np.zeros(N0)
    c3[712:] = 40.0
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 760, 100.0)), c=c3, cp_kw=dict(q_c=30.0))
    sm = run1(ps, cp, cfg, COMBO)
    t = tr(sm)
    assert L.CODES[int(t["code"])] == "S3" and t["u_dec"] == 712, t


@test
def t14_session_forced_exit_and_borders():
    N = 1500
    sess = np.zeros(N, bool)
    sess[600:1000] = True
    ps, cp, cfg = make_pair(lvlA=lvl(N, (700, N, 100.0)), sess=sess, cp_kw=dict(session=True))
    sm = run1(ps, cp, cfg, L.Combo(25.2, 3, 1, 1440, None), period0=600)
    t = tr(sm)
    assert L.CODES[int(t["code"])] == "SES" and t["u_dec"] == 997 and t["f_out"] == 999, t
    ps, cp, cfg = make_pair(lvlA=lvl(N, (650, N, 100.0)), sess=sess, cp_kw=dict(session=True))
    assert len(run1(ps, cp, cfg, L.Combo(25.2, 3, 1, 1440, None), period0=600).trades) == 0     # первые 60 минут после открытия - вход запрещён
    ps, cp, cfg = make_pair(lvlA=lvl(N, (996, N, 100.0)), sess=sess, cp_kw=dict(session=True))
    assert len(run1(ps, cp, cfg, L.Combo(25.2, 3, 1, 1440, None), period0=600).trades) == 0     # последние 3 минуты - вход запрещён


@test
def t15_dedup_common_leg():
    lv = lvl(N0, (700, 705, 100.0))
    legA = ("exA", "SYM")
    p1, cp, cfg = make_pair(lvlA=lv, key=("a", "b1"), legs=(legA, ("exB", "B1")))
    p2, _, _ = make_pair(lvlA=lv, key=("a", "b2"), legs=(legA, ("exC", "B2")))
    fts = [L.Feats(p, cp, cfg, 100) for p in (p1, p2)]
    s1, s2 = L.simulate_component([p1, p2], cp, cfg, COMBO, fts, 100)
    assert len(s1.trades) == 1 and len(s2.trades) == 0 and s2.cnt["dedup_dropped"] == 1, (len(s1.trades), len(s2.trades), dict(s2.cnt))
    cfg2 = L.Cfg(**dict(CFG, dedup=False))
    fts = [L.Feats(p, cp, cfg2, 100) for p in (p1, p2)]
    a, b = L.simulate_component([p1, p2], cp, cfg2, COMBO, fts, 100)
    assert len(a.trades) == 1 and len(b.trades) == 1


@test
def t16_funding_hand_computed():
    fa = {702: 0.01, 705: 0.001, 707: 0.01}
    fb = {706: 0.0005}
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 705, 100.0)), funA=fa, funB=fb)
    t = tr(run1(ps, cp, cfg, COMBO))
    assert t["f_in"] == 702 and t["f_out"] == 707
    want_f = 1e4 * (1.0 * 0.001 - 1.0 * 0.0005)                    # цена ноги A = open_in -> отношение 1; выплаты на f_in и f_out не начисляются
    assert close(t["fund"], want_f, 1e-9), (t["fund"], want_f)
    want_g = 1e4 * (0.0 - (math.exp(-0.01) - 1.0))
    assert close(t["net"], want_g - 12.6 + want_f, 1e-6)


@test
def t17_basis_sigma_vs_naive_median():
    rng = np.random.default_rng(3)
    N = 900
    s = rng.normal(5, 3, N)
    s[rng.random(N) < 0.3] = np.nan
    b, sig, cnt = L.basis_sigma(s, 300, 5, 60, 0.5)
    for t in (60, 61, 300, 301, 497, 600, 899):
        r = t // 5 * 5
        w = [x for x in s[max(0, r - 300):r] if math.isfinite(x)]
        if len(w) < 60:
            assert math.isnan(b[t])
            continue
        w.sort()
        med = 0.5 * (w[(len(w) - 1) // 2] + w[len(w) // 2])
        dv = sorted(abs(x - med) for x in w)
        mad = 0.5 * (dv[(len(dv) - 1) // 2] + dv[len(dv) // 2])
        assert close(b[t], med, 1e-9) and close(sig[t], max(1.4826 * mad, 0.5), 1e-9), (t, b[t], med)


# ------------------------------------------------------------------ G-C2-2
def rand_pair(seed, N=2500):
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.normal(0, 2.0, N))
    lA = common + rng.normal(0, 0.4, N)
    lB = common + rng.normal(0, 0.4, N)
    for _ in range(14):
        st = int(rng.integers(300, N - 100))
        ln = int(rng.integers(1, 90))
        amp = float(rng.choice([-1, 1]) * rng.uniform(30, 400))
        (lA if rng.random() < 0.6 else lB)[st:st + ln] += amp
    gapsA, gapsB = [], []
    for _ in range(int(rng.integers(0, 6))):
        st = int(rng.integers(100, N - 60))
        (gapsA if rng.random() < 0.5 else gapsB).append((st, st + int(rng.integers(1, 45))))
    cc = np.cumsum(rng.normal(0, 0.15, N))
    cc[int(rng.integers(500, 2000)):] += rng.choice([0, 0, 35.0])
    return lA, lB, gapsA, gapsB, cc


@test
def t18_vector_vs_naive_40_lines():
    n_lines = 0
    combos = [L.Combo(25.2, 3, 1, 120, 2.0), L.Combo(40.0, 5, 3, 360, None), L.Combo(25.2, 5, 1, 1440, 2.0), L.Combo(80.0, 3, 3, 120, None)]
    edges, vals = np.array([25.2, 40.0, 80.0, 160.0, 320.0, 640.0]), np.array([10.0, 10.0, 15.0, 20.0, np.nan, np.nan])
    for seed in range(12):
        lA, lB, gA, gB, cc = rand_pair(seed)
        kw = dict(kstale=20 + (seed % 3) * 10, m_r=3 + seed % 4, q_c=(35.0 if seed % 4 == 1 else math.inf), m_shift=(8.0 if seed % 3 == 0 else math.inf),
                  gmax=(500.0 if seed % 2 == 0 else math.inf), a90_edges=(edges if seed % 2 == 0 else None), a90_vals=(vals if seed % 2 == 0 else None))
        fa = {400 + 97 * j: 0.0004 * (j % 3 - 1) for j in range(20)}
        fb = {410 + 131 * j: 0.0003 for j in range(15)}
        ps, cp, cfg = make_pair(N=len(lA), lvlA=lA, lvlB=lB, gapA=gA, gapB=gB, c=cc, cp_kw=kw, funA=fa, funB=fb)
        ft = L.Feats(ps, cp, cfg, 200)
        for cb in combos:
            sim = L.simulate_component([ps], cp, cfg, cb, [ft], 200)[0]
            ntr, ncnt = NV.naive_pair(ps.oA, ps.cA, ps.oB, ps.cB, ps.c, ps.b, ps.sig, ps.r6, cp, cfg, cb, 200, border=ps.border, fcA=dict(fa), fcB=dict(fb))
            got = [(x[0], x[1], x[2], x[3], x[4], L.CODES[int(x[7])], x[8], x[11]) for x in sim.trades]
            assert len(got) == len(ntr), (seed, cb, len(got), len(ntr), got[:3], ntr[:3])
            for g_, n_ in zip(got, ntr):
                assert g_[:6] == n_[:6], (seed, cb, g_, n_)
                assert close(g_[6], n_[6], 1e-7) and close(g_[7], n_[7], 1e-7), (seed, cb, g_, n_)
            for k_ in ("signal_total", "entry_deferred", "signal_expired", "exit_ffill", "open_at_end", "pair_excluded"):
                assert sim.cnt.get(k_, 0) == ncnt.get(k_, 0), (seed, cb, k_, dict(sim.cnt), ncnt)
            n_lines += 1
    assert n_lines >= 40, n_lines
    print("   строк «пара x комбинация»: %d" % n_lines)


# ------------------------------------------------------------------ G-C2-5, G-C2-9 (защита)
@test
def t19_window_guard_red():
    d = tempfile.mkdtemp()
    os.environ["C2_HOME"] = d
    pf = os.path.join(d, "params.csv")
    open(pf, "w").write("a,b\n")
    sf = os.path.join(d, "conv_run.py")
    open(sf, "w").write("# stub\n")
    try:
        G.configure(mode="calib", params_paths=(pf, None), script_path=sf)
        for last in (2479, 2480, 2499):
            try:
                G.check_days(last)
                raise AssertionError("calib принял last_day=%d" % last)
            except SystemExit as e:
                assert e.code == 3
        G.configure(mode="window", go_window=False, params_paths=(pf, None), script_path=sf)
        try:
            G.check_days(2479)
            raise AssertionError("окно прочитано без --go-window")
        except SystemExit as e:
            assert e.code == 3
        G.configure(mode="window", go_window=True, params_paths=(pf, None), script_path=sf, runner=G.RUNNER)
        try:
            G.check_days(2479)
            raise AssertionError("окно прочитано без токена")
        except SystemExit as e:
            assert e.code == 3
        open(os.path.join(d, "go_window.token"), "w").write("deadbeef,\n" + G.sha256_file(sf) + "\nGO 07.11, CEO\n")
        try:
            G.check_days(2479)
            raise AssertionError("неверный токен принят")
        except SystemExit as e:
            assert e.code == 3
        open(os.path.join(d, "go_window.token"), "w").write(G.params_sha_line() + "\nbad\nGO 07.11, CEO\n")
        try:
            G.check_days(2479)
            raise AssertionError("токен с неверным sha скрипта принят")
        except SystemExit as e:
            assert e.code == 3
        open(os.path.join(d, "go_window.token"), "w").write(G.token_text("GO 07.11, CEO"))            # токен v2: манифест всего кода и входных файлов (дельта 07.11)
        G.STATE.git_checked = True                       # git-проверка params проверяется отдельно; здесь проверяем токен и журнал
        G.check_days(2479)
        assert os.path.isfile(os.path.join(d, "window_reads.log")), "журнал чтений окна не записан"
        G.configure(mode="train", params_paths=(pf, None), script_path=sf)
        try:
            G.check_days(2472)
            raise AssertionError("Pi_train принял last_day > 2471")
        except SystemExit as e:
            assert e.code == 3
        G.check_days(2471)
        G.configure(mode="calib", params_paths=(pf, None), script_path=sf)
        G.check_days(2478)
    finally:
        os.environ.pop("C2_HOME", None)
        G.configure(mode="calib")


@test
def t20_panel_reader_offsets_and_guard():
    """Читатель панелей читает ровно запрошенные сутки по смещению; запрос суток >= 2479 без токена - отказ ДО открытия файла."""
    import struct
    d = tempfile.mkdtemp()
    os.environ["C2_HOME"] = d
    try:
        first = 2465 * 1440
        n = 20 * 1440
        o = np.arange(n, dtype="<f8") + 1.0
        c = o + 0.5
        o[100:110] = np.nan
        c[100:110] = np.nan
        p = os.path.join(d, "X.sap")
        with open(p, "wb") as f:
            f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first, n))
            f.write(o.tobytes() + c.tobytes())
        G.configure(mode="test", params_paths=())
        oo, cc = G.load_panel_range(p, 2465, 2478)
        assert len(oo) == 14 * 1440 and close(oo[0], 1.0) and close(cc[5], 6.5) and np.isnan(oo[105])
        assert close(oo[-1], 14 * 1440.0) and close(cc[-1], 14 * 1440.0 + 0.5)        # последняя минута формирования, не окна
        G.configure(mode="calib", require_k0=False)
        G.STATE.git_checked = True
        oo, cc = G.load_panel_range(p, 2466, 2470)
        assert len(oo) == 5 * 1440 and close(oo[0], 1441.0)
        try:
            G.load_panel_range(p, 2465, 2479)
            raise AssertionError("прочитаны сутки 2479 без токена")
        except SystemExit as e:
            assert e.code == 3
    finally:
        os.environ.pop("C2_HOME", None)
        G.configure(mode="calib")


# ------------------------------------------------------------------ синтетический мир для CLI-тестов (детерминизм, защита)
def write_world(d, n_assets=6, exs=("aex", "bex", "cex"), days=14, seed=7):
    """Панели .sap (формирование 2465..2478) + asset_pairs.csv + funding; цены с тиком 0,01, всплески в ногах."""
    import struct
    rng = np.random.default_rng(seed)
    n = days * 1440
    first = 2465 * 1440
    rows = ["key,scope,big1,big2,class_match,ex1,sym1,role1,ex2,sym2,role2,quote1,quote2,c1d2"]
    for ai in range(n_assets):
        base = 50.0 + 20 * ai
        common = np.cumsum(rng.normal(0, 1.2, n))
        series = {}
        for ex in exs:
            lg = common + rng.normal(0, 0.3, n)
            for _ in range(10):
                st = int(rng.integers(1500, n - 200))
                ln = int(rng.integers(1, 40))
                lg[st:st + ln] += float(rng.choice([-1, 1]) * rng.uniform(30, 200))
            c = np.round(base * np.exp(lg / 1e4), 2)
            o = np.concatenate([[c[0]], c[:-1]])
            gaps = rng.random(n) < 0.02
            c[gaps] = np.nan
            o[gaps] = np.nan
            series[ex] = (o, c)
            os.makedirs(os.path.join(d, "panels", ex), exist_ok=True)
            with open(os.path.join(d, "panels", ex, "SYM%dUSDT.sap" % ai), "wb") as f:
                f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first, n))
                f.write(o.astype("<f8").tobytes() + c.astype("<f8").tobytes())
        for i, e1 in enumerate(exs):
            for e2 in exs[i + 1:]:
                rows.append("A%d,между биржами,крипта,крипта,1,%s,SYM%dUSDT,main,%s,SYM%dUSDT,main,USDT,USDT,1" % (ai, e1, ai, e2, ai))
    open(os.path.join(d, "asset_pairs.csv"), "w", encoding="utf-8").write(chr(10).join(rows) + chr(10))


def sha_tree(d):
    import hashlib
    h = {}
    for root, _, fs in os.walk(d):
        for f in sorted(fs):
            if f.endswith(".npy") or f.endswith(".json") or f.endswith(".csv"):
                p = os.path.join(root, f)
                if f in ("series_summary.json", "meta.json"):
                    continue                              # содержат время счёта
                h[os.path.relpath(p, d)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return h


def run_cli(args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, os.path.join(HERE, "conv_run.py")] + args, capture_output=True, text=True, env=e)


@test
def t21_determinism_threads_and_repeat():
    """G-C2-6: два запуска и 1/4/8 потоков дают побайтно равные выходы."""
    tw = tempfile.mkdtemp()
    write_world(tw)
    cf = os.path.join(tw, "combos.json")
    json.dump([[25.2, 3, 1, 120, 2.0], [40.0, 3, 3, 360, None], [25.2, 5, 1, 1440, 2.0], [80.0, 3, 1, 120, None]], open(cf, "w"))
    hashes = []
    for i, th in enumerate((1, 1, 4, 8)):
        out = tempfile.mkdtemp()
        base = ["--test-world", tw, "--out", out, "--last-day", "2478", "--threads", str(th), "--nb", "240", "--no-turn"]
        r = run_cli(["series"] + base)
        assert r.returncode == 0, r.stderr[-800:]
        r = run_cli(["grid"] + base + ["--tag", "t", "--combos", cf, "--trades-out"])
        assert r.returncode == 0, r.stderr[-800:]
        hashes.append(sha_tree(out))
    assert len(hashes[0]) > 8, hashes[0].keys()
    for h in hashes[1:]:
        assert h == hashes[0], [k for k in h if h[k] != hashes[0].get(k)]
    ntr = open(os.path.join(out, "calib", "grid_t", "crypto", "trades.csv")).read().count(chr(10)) - 1
    assert ntr > 0, "в синтетическом мире нет сделок - проверка пуста"
    print("   потоки 1/1/4/8 побайтно равны; файлов %d, сделок %d" % (len(hashes[0]), ntr))


@test
def t22_cli_refuses_window_without_token():
    """G-C2-5 через CLI: отказ код 3 до чтения данных."""
    d = tempfile.mkdtemp()
    env = {"C2_HOME": d}
    for args in (["series", "--last-day", "2479"], ["series", "--last-day", "2499", "--mode", "window"], ["series", "--last-day", "2499", "--mode", "window", "--go-window"],
                 ["grid", "--last-day", "2472", "--mode", "train"], ["grid", "--last-day", "2480", "--mode", "calib"]):
        r = run_cli(args + ["--out", d], env)
        assert r.returncode == 3, (args, r.returncode, r.stderr[-300:])
    open(os.path.join(d, "go_window.token"), "w").write(chr(10).join(["x", "y", "GO 07.11, CEO"]) + chr(10))
    r = run_cli(["series", "--last-day", "2479", "--mode", "window", "--go-window", "--out", d], env)
    assert r.returncode == 3, (r.returncode, r.stderr[-300:])
    assert not os.path.exists(os.path.join(d, "window_reads.log")), "журнал записан при отказе"


# ------------------------------------------------------------------ калибровка и агрегаты (known answer)
@test
def t23_aj_km_known_answers():
    import conv_calib as C
    f1, f2 = C.aj_cif([1, 2, 3, 4], [1, 2, 1, 2])
    assert close(f1, 0.5, 1e-12) and close(f2, 0.5, 1e-12), (f1, f2)
    f1, f2 = C.aj_cif([1, 2, 3, 4, 5], [1, 0, 0, 0, 2])          # цензура: риск 1/5, затем 4 в риске ... вручную: S: 0.8 (t=1), цензура t=2..4, t=5: nr=1
    assert close(f1, 0.2, 1e-12) and close(f2, 0.8, 1e-12), (f1, f2)
    assert C.km_quantile([1, 2, 3, 4, 5], [1, 1, 1, 1, 0], 0.5) == 3.0
    assert C.km_quantile([1, 2, 3, 4, 5], [1, 1, 1, 1, 0], 0.99) is None
    lo1, hi1, lo2, hi2 = C.aj_cluster_bootstrap(np.array([1, 2, 3, 4] * 25), np.array([1, 2, 1, 2] * 25), np.arange(100), B=300, seed=37)
    assert lo1 < 0.5 < hi1 and lo2 < 0.5 < hi2, (lo1, hi1, lo2, hi2)


@test
def t24_halflife_known_phi():
    import conv_calib as C
    rng = np.random.default_rng(11)
    phi1 = 0.99
    n = 14 * 1440
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi1 * x[i - 1] + rng.normal(0, 1.0)
    x0, x1 = x[:-15], x[15:]
    dayidx = np.arange(15, n) // 1440
    st = np.zeros((14, 6))
    for dd in range(14):
        m = dayidx == dd
        xa, ya = x0[m], x1[m]
        st[dd] = [m.sum(), xa.sum(), ya.sum(), (xa * xa).sum(), (xa * ya).sum(), (ya * ya).sum()]
    r = C.hl15_from_days(st, range(1, 14), B=500)
    true_hl = 15 * math.log(2) / -math.log(phi1 ** 15)
    assert abs(r["hl15"] - true_hl) / true_hl < 0.25, (r, true_hl)
    assert r["hl_hi"] >= r["hl15"] and r["n_reg"] > 300
    assert C.q5_admitted(r, 4 * r["hl_hi"] + 1) and not C.q5_admitted(r, 4 * r["hl_hi"] - 10)
    r2 = C.hl15_from_days(st[:3], range(1, 3))                       # меньше 3 суток наблюдений - не допускается
    assert r2["hl_hi"] is None


@test
def t25_episode_outcomes():
    import conv_calib as C
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 705, 100.0), (900, 903, 100.0), (903, 915, 250.0)))
    eps = C.collect_episodes([ps], cp, cfg, 100)
    assert len(eps) == 2, eps
    e1, e2 = eps
    assert e1[0] == 700 and e1[3] == 1 and e1[4] == 5, e1                    # (К), u = 705, длительность 5
    assert e2[0] == 900 and e2[3] == 2 and e2[5] >= 150 - 1e-9, e2           # (С): x = 250 >= 2*100; неблагоприятное смещение 150


@test
def t26_agg_on_synthetic_grid():
    import conv_agg as A
    tw = tempfile.mkdtemp()
    write_world(tw, n_assets=8)
    out = tempfile.mkdtemp()
    base = ["--test-world", tw, "--out", out, "--last-day", "2478", "--threads", "2", "--no-turn"]
    assert run_cli(["series"] + base).returncode == 0
    r = run_cli(["grid"] + base + ["--tag", "g"])
    assert r.returncode == 0, r.stderr[-600:]
    gd = os.path.join(out, "calib", "grid_g")
    gr = A.Grid(gd, "crypto")
    ci = gr.cindex[(25.2, 3, 1, 120, 2.0)]
    rows = np.arange(len(gr.pids))
    ser = A.y_series(gr, ci, rows)
    t = gr.tab[ci]
    nd = gr.nd
    for j, d in enumerate(ser["days"]):
        dd = d - gr.d0
        want = t[:, dd, A.FI["net"]].sum() / gr.act[ci][:, dd].sum()
        assert close(ser["closed"][j], want, 1e-9)
    rev = A.y_series(gr, ci, rows, rev=True)
    n_all = t[:, :, A.FI["n"]].sum()
    assert close(sum(t[:, :, A.FI["net"]].flatten()) + (-t[:, :, A.FI["gross"]] - 12.6 * t[:, :, A.FI["n"]] - t[:, :, A.FI["fund"]]).sum(), -25.2 * n_all, 1e-6)
    best, table = A.class_selection(gr)
    assert len(table) == 180 and all("mean" in r_ for r_ in table)
    res = A.class_formation(gr, gr, "crypto")
    assert "reasons" in res and "a3b" in res or res["formed"] is False
    pw = A.perm_pwin(gr, rows, n_perm=10)
    assert pw is None or 0 < pw["p_win"] <= 1


@test
def t27_controls_shift_and_random():
    import conv_ctrl as CT
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 705, 100.0), (900, 905, 100.0)))
    ft = L.Feats(ps, cp, cfg, 100)
    sim = L.simulate_component([ps], cp, cfg, COMBO, [ft], 100)[0]
    assert len(sim.trades) == 2
    out, done, skipped = CT.shifted_entries(ps, cp, cfg, COMBO, ft, sim.trades, 0, 100)           # сдвиг 0 воспроизводит сделки
    assert done == 2 and close(sum(out.values()), sum(tr(sim, i)["net"] for i in range(2)), 1e-9), (out, done)
    out5, d5, s5 = CT.shifted_entries(ps, cp, cfg, COMBO, ft, sim.trades, 15, 100)                 # через 15 минут порога нет
    assert d5 == 0 and s5 == 2
    rng = np.random.default_rng(37)
    out_r, done_r = CT.random_entries(ps, cp, cfg, COMBO, ft, 6, [5], 100, rng)
    assert done_r > 0 and all(isinstance(k, (int, np.integer)) for k in out_r)


def main():
    names = sys.argv[1:]
    fails = 0
    for fn in OK:
        if names and fn.__name__ not in names and not any(fn.__name__.startswith(x) for x in names):
            continue
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception:                                    # noqa: BLE001
            fails += 1
            print("FAIL", fn.__name__)
            traceback.print_exc()
    print("итог: %d из %d тестов не прошли" % (fails, len([f for f in OK if not names or f.__name__ in names or any(f.__name__.startswith(x) for x in names)])))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
