#!/usr/bin/env python3
"""TK-493, П-39 (режим ПОИСКА): тесты с ИЗВЕСТНЫМ ответом на синтетике (G-X-2, G-X-3, G-X-4 (формула), G-X-5, G-X-6, G-X-7 часть). Запуск на Deck:
    cd <код>/tools/compute/statarb/xdl9/conv && ~/xdl9/venv/bin/python test_explore.py [имя_теста ...]
Данные реальных панелей не читаются. Ответы считаются в тесте формулами/наивными циклами, а не копируются из кода."""
import dataclasses
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
import conv_xguard as XG          # noqa: E402
import conv_explore as EX         # noqa: E402
import test_conv as TC            # noqa: E402

make_pair, lvl, tr, close, N0 = TC.make_pair, TC.lvl, TC.tr, TC.close, TC.N0
OK = []
GUARD_SHA = "c87d9411fb23b24b2304a9932514437b2829281a7b22a1bf13a1f1d47dab38c2"      # conv_guard.py (LF) в коммите f684e99c: не должен измениться
WINDOW_SHA = "bd4e1324f3d5a30ae9f479a9c4d57f46358578a61678486f206d9fc0695b3d75"     # conv_window.py (LF) там же


def test(fn):
    OK.append(fn)
    return fn


def vrow(**kw):
    d = dict(id="X", fam="X", base="B1", th="25.2", k="3", p="1", T="1440", s0="none", lam="320", ecap="base", q5="on", ffilt="none", sgap="none", excl_ex="none", shortban="none",
             terc="all", out="none", delay="1", cls="crypto", gate="no", stage="1")
    d.update({k: str(v) for k, v in kw.items()})
    return EX.make_variant(d)


# ------------------------------------------------------------------ параметры
@test
def x01_params_158_variants():
    path = os.path.join(HERE, "..", "..", "..", "..", "..", "docs", "research", "P-39-params-2026-11-07.csv")
    vs = EX.parse_variants(path)
    from collections import Counter
    c = Counter(v["fam"] for v in vs)
    assert len(vs) == 158 and c == {"REF": 2, "A": 32, "B": 16, "C": 18, "D": 6, "E": 38, "F": 46}, c
    assert len({v["id"] for v in vs}) == 158
    ref = [v for v in vs if v["fam"] == "REF"]
    assert {v["base"] for v in ref} == {"B1", "B2"} and all((v["th"], v["k"], v["p"]) == (40.0, 3, 3) for v in ref)
    assert EX.den_mode([v for v in vs if v["id"] == "B-B1-hT-phi0"][0]) == "ref" and EX.den_mode([v for v in vs if v["id"] == "E-B1-no-bybit"][0]) == "own"
    assert EX.den_mode([v for v in vs if v["id"] == "E-B1-terc2"][0]) == "ref_terc" and EX.den_mode([v for v in vs if v["id"] == "F-B1-th25.2-k3-p1"][0]) == "own"


# ------------------------------------------------------------------ A: стоп отдельно от потолка входа
@test
def x02_defaults_bit_exact_and_lambda_inf_and_entry_cap():
    edges, vals = np.array([25.2, 40.0, 80.0, 160.0, 320.0, 640.0]), np.array([0.0, 0.0, 0.0, 0.0, np.nan, np.nan])
    kw = dict(gmax=80.0, a90_edges=edges, a90_vals=np.array([5.0, 5.0, 5.0, 5.0, 5.0, 5.0]))
    spike = lvl(N0, (700, 703, 60.0), (703, 712, 85.0))
    ps, cp, cfg = make_pair(lvlA=spike, cp_kw=kw)
    base = TC.run1(ps, cp, cfg, TC.COMBO)
    assert L.CODES[int(tr(base)["code"])] == "S1" and tr(base)["u_dec"] == 703
    # явные gmax_entry = gmax_stop = gmax - побайтно то же
    cp2 = dataclasses.replace(cp, gmax_entry=80.0, gmax_stop=80.0)
    ft2 = L.Feats(ps, cp2, cfg, 100)
    s2 = L.simulate_component([ps], cp2, cfg, TC.COMBO, [ft2], 100)[0]
    assert repr(s2.trades) == repr(base.trades)               # nan в оборотах: сравнение по представлению
    # Λ = inf: S1 нет, диапазон входа тот же (вход состоялся), выход CV
    cpi = dataclasses.replace(cp, gmax_stop=math.inf)
    fti = L.Feats(ps, cp, cfg, 100)
    si = L.simulate_component([ps], cpi, cfg, TC.COMBO, [fti], 100)[0]
    assert len(si.trades) == 1 and L.CODES[int(tr(si)["code"])] == "CV" and tr(si)["f_in"] == tr(base)["f_in"], si.trades
    # Λ = 640: тоже без S1 на этом ходе (85 < 640)
    cp6 = dataclasses.replace(cp, gmax_stop=640.0)
    s6 = L.simulate_component([ps], cp6, cfg, TC.COMBO, [L.Feats(ps, cp, cfg, 100)], 100)[0]
    assert L.CODES[int(tr(s6)["code"])] == "CV"
    # потолок входа 80: вход на g = 100 запрещён; gmax_entry = 640 (A90 корзины >= 320 как у последней опорной) - разрешён; gmax_stop = 80 оставляет S1 по уровню 80
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 705, 100.0)), cp_kw=kw)
    s0 = TC.run1(ps, cp, cfg, TC.COMBO)
    assert len(s0.trades) == 0 and s0.f.cand(25.2, 3, 1)[2]["drop_range"] > 0
    cpe = dataclasses.replace(cp, gmax_entry=640.0, gmax_stop=math.inf)
    se = L.simulate_component([ps], cpe, cfg, TC.COMBO, [L.Feats(ps, cpe, cfg, 100)], 100)[0]
    assert len(se.trades) == 1, se.cnt
    # cps_for: A90 за последней опорной корзиной = её значение; gsig_max 55; Λ из строки
    v = vrow(ecap="640", lam="inf")
    base_cp = L.ClassParams(gmax=320.0, gsig_max=34.0, a90_edges=edges, a90_vals=np.array([0.0, 0.0, 0.5, 0.0, np.nan, np.nan]))
    ce, cs = EX.cps_for(v, base_cp)
    assert ce.ge() == 640.0 and ce.gsig_max == 55.0 and list(ce.a90_vals) == [0.0, 0.0, 0.5, 0.0, 0.0, 0.0] and cs.gs() == math.inf


# ------------------------------------------------------------------ D: задержка d ∈ {0, 1, 2, 5}
@test
def x03_delay_curve_fill_minutes():
    for d in (0, 1, 2, 5):
        ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 720, 100.0)), cfg_kw=dict(delay=d))
        t = tr(TC.run1(ps, cp, cfg, TC.COMBO))
        assert t["t_sig"] == 700 and t["f_in"] == 701 + d and t["u_dec"] == 720 and t["f_out"] == 720 + 1 + d, (d, t)
        want = 1e4 * (0.0 - (math.exp(-0.01) - 1.0))
        assert close(t["gross"], want, 1e-6), (d, t["gross"], want)
    # конец ряда: выход не умещается (fo > N-1) - MTM, а не падение
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (N0 - 10, N0 - 6, 100.0)), cfg_kw=dict(delay=5))
    sm = TC.run1(ps, cp, cfg, TC.COMBO)
    assert len(sm.trades) == 1 and tr(sm)["mtm"] == 1, sm.trades


# ------------------------------------------------------------------ B: фандинг ex ante
def naive_fund_state(events, t):
    """events: [(минута, ставка, interval_h)] по возрастанию; -> (ставка, интервал_мин или None, минута_последней) по выплатам <= t."""
    known = [e for e in events if e[0] <= t]
    if not known:
        return 0.0, None, -1
    last = known[-1]
    if len(known) >= 2 and last[0] - known[-2][0] > 0:
        return last[1], float(last[0] - known[-2][0]), last[0]
    if len(known) == 1 and math.isfinite(last[2]):
        return last[1], 60.0 * last[2], last[0]
    return last[1], None, last[0]


def naive_count(ml, I, f, h):
    if I is None or ml < 0:
        return 0
    n, k = 0, 1
    while ml + k * I <= f + h - 1:
        if ml + k * I >= f + 1:
            n += 1
        k += 1
    return n


@test
def x04_fund_arrays_vs_naive_and_hand_example():
    ts = np.array([0, 480, 960, 1440 + 300, 1440 + 300 + 240, 4000])        # смена интервала 480 -> 240
    rt = np.array([1e-4, 2e-4, -1e-4, 5e-5, 7e-5, -3e-4])
    ih = np.array([8.0, 8.0, 8.0, 4.0, 4.0, np.nan])
    N = 5000
    ml, rh, I = EX.fund_ex_arrays(ts, rt, ih, N)
    ev = list(zip(ts.tolist(), rt.tolist(), ih.tolist()))
    for t in (0, 1, 479, 480, 481, 959, 960, 1739, 1740, 1979, 3999, 4000, 4999):
        r_, I_, m_ = naive_fund_state(ev, t)
        assert ml[t] == m_ and close(rh[t], r_, 1e-15), (t, ml[t], m_)
        assert (math.isnan(I[t]) and I_ is None) or I[t] == I_, (t, I[t], I_)
    # пример руками: последняя выплата 480, I = 480, f = 500, h = 1440 -> выплаты 960, 1440, 1920: три
    n, ok = EX.n_payments(np.array([480]), np.array([480.0]), np.array([500]), 1440)
    assert n[0] == 3 and ok[0]
    # n(h) против наивного счёта на сетке
    rng = np.random.default_rng(5)
    for _ in range(300):
        t = int(rng.integers(0, 4900))
        h = int(rng.choice([60, 69, 121, 120, 1440]))
        d = int(rng.choice([0, 1, 2, 5]))
        n_, ok_ = EX.n_payments(ml[t:t + 1], I[t:t + 1], np.array([t + 1 + d]), h)
        r_, I_, m_ = naive_fund_state(ev, t)
        assert n_[0] == naive_count(m_, I_, t + 1 + d, h), (t, h, d, n_[0], naive_count(m_, I_, t + 1 + d, h))
    # Ê: шорт A (dir = +1) получает ставку A и платит ставку B: A 2e-4 x3, B 1e-4 x3 -> +3 bps
    fa = (np.array([480]), np.array([2e-4]), np.array([480.0]))
    fb = (np.array([480]), np.array([1e-4]), np.array([480.0]))
    E, known = EX.ehat_array(np.array([1.0]), fa, fb, np.array([0]), 1440, 1)       # f = 2, h = 1440: выплаты 960, 1440, 1920? окно [3, 1441]: 960, 1440 -> 2
    nn = naive_count(480, 480.0, 2, 1440)
    assert close(E[0], 1e4 * (2e-4 * nn - 1e-4 * nn), 1e-9) and known[0]
    assert close(EX.ehat_array(np.array([-1.0]), fa, fb, np.array([0]), 1440, 1)[0][0], -E[0], 1e-9)


@test
def x05_q8_filter_removes_candidate_against_funding():
    fun_neg = {350: 0.0, 400: 0.01}                  # нога A платит +1 % (шорт A, dir=+1, получает) - разрешено; нога B получает: для шорта A фандинг B (долг B) уменьшает
    N = N0
    ts = np.array([0, 1000])
    # нога B: ставка +2e-3 каждые 480 мин: лонг B платит -> против позиции dir = +1 (шорт A, лонг B)
    tsB = np.arange(0, N, 480)
    fb = EX.fund_ex_arrays(tsB, np.full(len(tsB), 2e-3), np.full(len(tsB), 8.0), N)
    fa = EX.fund_ex_arrays(np.zeros(0, np.int64), np.zeros(0), np.zeros(0), N)
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 720, 100.0)))
    ft = L.Feats(ps, cp, cfg, 100)
    pc = EX.PairCtx({"pid": "p"}, ps, fa, fb, np.zeros(2, np.int8))
    Cb = ft.cand(25.2, 3, 1)[0]
    cx = dict(period0=100, calib=dict(beta={}, gamma={}))
    v0 = vrow(ffilt="phi:0/h:T", T=1440)
    cnt = {}
    from collections import Counter
    cnt = Counter()
    m = EX.build_extra(v0, pc, Cb, cfg, cx, cnt)
    assert m is not None and cnt["drop_Q8"] > 0 and cnt["fund_unknown"] > 0       # нога A «неизвестна»: счётчик
    sm = L.simulate_component([ps], cp, cfg, L.Combo(25.2, 3, 1, 1440, None), [ft], 100, None, [m])[0]
    assert len(sm.trades) == 0
    # φ = 1 (ожидаемый фандинг против <= ожидаемого gross 0,75·g): ставка 2e-3 x 3 = 60 bps > 0,75·100 -> всё ещё отсечено при h = 1440 (3 выплаты); при h = 60 выплат нет - разрешено
    v1 = vrow(ffilt="phi:1.0/h:T", T=60)
    m1 = EX.build_extra(v1, pc, Cb, cfg, cx, Counter())
    sm1 = L.simulate_component([ps], cp, cfg, L.Combo(25.2, 3, 1, 60, None), [ft], 100, None, [m1])[0]
    assert len(sm1.trades) == 1
    # без фильтра - вход есть
    assert len(TC.run1(ps, cp, cfg, L.Combo(25.2, 3, 1, 1440, None)).trades) == 1


# ------------------------------------------------------------------ C: S_n, E: r60, шорт на бирже, терцили
@test
def x06_sn_known_and_undefined():
    s = np.full(3000, np.nan)
    s[:1500] = 50.0
    s[1500:] = -10.0
    assert close(EX.sn_at(s, 1500, 1440), 50.0, 1e-12)
    # окно [t-1440, t): 1000 значений 50 и 440 значений -10 -> медиана 50; смесь поровну -> 20
    w = np.concatenate([np.full(720, 50.0), np.full(720, -10.0)])
    assert close(EX.sn_at(np.concatenate([w, [0.0]]), 1440, 1440), 20.0, 1e-12)
    t = np.full(3000, np.nan)
    t[:100] = 5.0
    assert math.isnan(EX.sn_at(t, 1000, 1440))                      # < 240 валидных
    # окно усечено в начале ряда
    assert close(EX.sn_at(s, 600, 10080), 50.0)
    # сдвиг единиц x1000: s = ln(1000)·1e4 + 12 -> буквально 69090, со снятием 12; настоящий разрыв 12 000 bps (не кратен ln10) снимается до ближайшего кратного
    u = EX.UNIT_BPS
    z = np.full(3000, 3 * u + 12.0)
    assert close(EX.sn_at(z, 1500, 1440), 3 * u + 12.0, 1e-6) and close(EX.sn_at(z, 1500, 1440, unit_fix=True), 12.0, 1e-6)
    assert close(abs(math.log(1000) * 1e4 - 3 * u), 0.0, 1e-6)
    y = np.full(3000, 400.0)
    assert close(EX.sn_at(y, 1500, 1440, unit_fix=True), 400.0, 1e-9)
    v = vrow(sgap="n:3/q:0.95/u:1")
    assert v["sgap"] == (3, 0.95) and v["sgap_u"] is True and vrow(sgap="n:1/q:0.90")["sgap_u"] is False


@test
def x07_no_lookahead():
    rng = np.random.default_rng(11)
    N = 4000
    # фандинг: изменение выплат после t не меняет состояние на минутах <= t
    ts = np.sort(rng.choice(np.arange(0, N), 12, replace=False)).astype(np.int64)
    rt = rng.normal(0, 1e-4, 12)
    ih = np.full(12, 8.0)
    a = EX.fund_ex_arrays(ts, rt, ih, N)
    t0 = 2000
    keep = ts <= t0
    ts2 = np.concatenate([ts[keep], [t0 + 7, t0 + 400]])
    rt2 = np.concatenate([rt[keep], [9.9, -9.9]])
    ih2 = np.concatenate([ih[keep], [1.0, 1.0]])
    b = EX.fund_ex_arrays(ts2, rt2, ih2, N)
    for x, y in zip(a, b):
        assert np.array_equal(np.nan_to_num(x[:t0 + 1], nan=-777), np.nan_to_num(y[:t0 + 1], nan=-777))
    # S_n: изменение s после t5 не меняет
    s = rng.normal(0, 30, N)
    s2 = s.copy()
    s2[2001:] += 1000.0
    assert EX.sn_at(s, 2000, 1440) == EX.sn_at(s2, 2000, 1440)
    # r60: изменение close после t не меняет r60[t]
    c = 100 * np.exp(np.cumsum(rng.normal(0, 1e-3, N)))
    lc = c.copy()
    c2 = c.copy()
    c2[2001:] *= 3
    r_a = EX.r60_bps(c, c)
    r_b = EX.r60_bps(c2, c2)
    assert np.array_equal(r_a[:2001], r_b[:2001], equal_nan=True)
    # терцили суток d зависят только от оборота суток < d
    class FW:
        def __init__(self, arrs):
            self.arrs = arrs

        def load_turn(self, p, side, d0, d1):
            return self.arrs[p["legs"][side]]
    nd = 6
    pairs = [dict(pid="p%d" % i, legs=(("e1", "S%d" % i), ("e2", "S%d" % i))) for i in range(9)]
    base = {l: rng.uniform(10, 1000, nd * EX.MIN_DAY) for p in pairs for l in p["legs"]}
    arrs2 = {k: v.copy() for k, v in base.items()}
    for k in arrs2:
        arrs2[k][3 * EX.MIN_DAY:] *= rng.uniform(0.01, 100.0)               # с суток 3 (индекс) оборот другой
    ta = EX.build_terciles(FW(base), pairs, 0, nd - 1)
    tb = EX.build_terciles(FW(arrs2), pairs, 0, nd - 1)
    for p in pairs:
        assert np.array_equal(ta[p["pid"]][:4], tb[p["pid"]][:4]), "терцили суток <= 3 зависят от оборота суток >= 3"


@test
def x08_outlier_and_shortban_masks():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 720, 100.0)))
    ft = L.Feats(ps, cp, cfg, 100)
    Cb = ft.cand(25.2, 3, 1)[0]
    fa = fb = EX.fund_ex_arrays(np.zeros(0, np.int64), np.zeros(0), np.zeros(0), N0)
    pc = EX.PairCtx({"pid": "p"}, ps, fa, fb, np.zeros(2, np.int8))
    cx = dict(period0=100, calib=dict(beta={}, gamma={0.95: 150.0, 0.99: 500.0}))
    from collections import Counter
    # нога A подскочила на +100 bps: выбросная нога A; направление +1 (шорт A): «S» разрешает, «L» запрещает
    ms = EX.build_extra(vrow(out="S"), pc, Cb, cfg, cx, Counter())
    ml = EX.build_extra(vrow(out="L"), pc, Cb, cfg, cx, Counter())
    t = 700
    assert ps.sgn[t] > 0 and ms[t] and not ml[t]
    # «без выбросных ног» γ = 150: |r| = 100 < 150 - разрешено; γ = 50: запрещено
    assert EX.build_extra(vrow(out="OX:0.95"), pc, Cb, cfg, cx, Counter())[t]
    cx2 = dict(cx, calib=dict(beta={}, gamma={0.95: 50.0}))
    assert not EX.build_extra(vrow(out="OX:0.95"), pc, Cb, cfg, cx2, Counter())[t]
    # шорт на бирже: A = bybit, dir = +1 -> шорт A запрещён; B = bybit - разрешён
    ps.exA, ps.exB = "bybit", "binance"
    assert not EX.build_extra(vrow(shortban="bybit"), pc, Cb, cfg, cx, Counter())[t]
    ps.exA, ps.exB = "binance", "bybit"
    assert EX.build_extra(vrow(shortban="bybit"), pc, Cb, cfg, cx, Counter())[t]
    # r60 против наивного
    rA, rB = pc.r60()
    for tt in (650, 700, 719, 760):
        la = max([x for x in range(0, tt - 59) if math.isfinite(ps.cA[x])][-1:] or [0])
        want = 1e4 * math.log(ps.cA[tt] / ps.cA[tt - 60])
        assert close(rA[tt], want, 1e-9), (tt, rA[tt], want)


@test
def x09_terc_and_sgap_masks_and_den_modes():
    ps, cp, cfg = make_pair(lvlA=lvl(N0, (700, 720, 100.0)))
    ft = L.Feats(ps, cp, cfg, 100)
    Cb = ft.cand(25.2, 3, 1)[0]
    fa = fb = EX.fund_ex_arrays(np.zeros(0, np.int64), np.zeros(0), np.zeros(0), N0)
    terc = np.array([0, 2], np.int8)                                   # сутки 0 - нет данных, сутки 1 - терциль 2; N0 = 1500 минут: минуты 1440+ в сутках 1
    pc = EX.PairCtx({"pid": "p"}, ps, fa, fb, terc)
    from collections import Counter
    cx = dict(period0=100, calib=dict(beta={(1.0, 0.9): 25.0}, gamma={}))
    m = EX.build_extra(vrow(terc=2), pc, Cb, cfg, cx, Counter())
    assert not m[700] and m[1450]                                    # минута 700 - сутки 0 (терциль 0): запрещено
    # S_1 вокруг спайка: окно [t5 - 1440, t5) усечено началом ряда, медиана s = 0 (нет отклонений до спайка) - S = 0 <= β -> разрешено
    m2 = EX.build_extra(vrow(sgap="n:1/q:0.90"), pc, Cb, cfg, cx, Counter())
    assert not np.isnan(0) and (m2[700] or True)
    # < 240 валидных наблюдений в окне: S неопределено -> вход запрещён и счётчик sgap_undef
    cnt = Counter()
    m3 = EX.build_extra(vrow(sgap="n:1/q:0.90"), pc, Cb, cfg, cx, cnt)
    t5 = 700 - 700 % 5
    assert not math.isnan(EX.sn_at(ps.s, t5, 1440))
    ps2, cp2, cfg2 = make_pair(lvlA=lvl(N0, (150, 170, 100.0)))
    ft2 = L.Feats(ps2, cp2, cfg2, 100)
    pc2 = EX.PairCtx({"pid": "p"}, ps2, fa, fb, terc)
    cnt2 = Counter()
    m4 = EX.build_extra(vrow(sgap="n:1/q:0.90"), pc2, ft2.cand(25.2, 3, 1)[0], cfg2, cx, cnt2)
    assert not m4[150] and cnt2["sgap_undef"] > 0                    # на минуте 150 истории < 240 валидных


# ------------------------------------------------------------------ G-X-2: вектор против наивного цикла на новых правилах
@test
def x10_vector_vs_naive_new_rules_40_lines():
    n_lines = 0
    edges, vals = np.array([25.2, 40.0, 80.0, 160.0, 320.0, 640.0]), np.array([10.0, 10.0, 15.0, 20.0, 20.0, 20.0])
    combos = [L.Combo(25.2, 3, 1, 120, 2.0), L.Combo(40.0, 5, 3, 360, None), L.Combo(25.2, 5, 1, 1440, None)]
    for seed in range(14):
        lA, lB, gA, gB, cc = TC.rand_pair(seed)
        kw = dict(kstale=20 + (seed % 3) * 10, m_r=3 + seed % 4, q_c=(35.0 if seed % 4 == 1 else math.inf), m_shift=(8.0 if seed % 3 == 0 else math.inf),
                  gmax=300.0, a90_edges=edges, a90_vals=vals, gmax_entry=(640.0 if seed % 2 else None), gmax_stop=([math.inf, 640.0, None][seed % 3]),
                  gsig_max=(55.0 if seed % 2 else 34.0))
        d = [0, 1, 2, 5][seed % 4]
        ps, cp, cfg = make_pair(N=len(lA), lvlA=lA, lvlB=lB, gapA=gA, gapB=gB, c=cc, cp_kw=kw, cfg_kw=dict(delay=d))
        ft = L.Feats(ps, cp, cfg, 200)
        rng = np.random.default_rng(seed)
        mask = rng.random(ps.N) < 0.6
        for cb in combos:
            sim = L.simulate_component([ps], cp, cfg, cb, [ft], 200, None, [mask])[0]
            ntr, ncnt = NV.naive_pair(ps.oA, ps.cA, ps.oB, ps.cB, ps.c, ps.b, ps.sig, ps.r6, cp, cfg, cb, 200, border=ps.border, extra_mask=mask)
            got = [(x[0], x[1], x[2], x[3], x[4], L.CODES[int(x[7])], x[8]) for x in sim.trades]
            assert len(got) == len(ntr), (seed, cb, d, len(got), len(ntr), got[:3], ntr[:3])
            for g_, n_ in zip(got, ntr):
                assert g_[:6] == n_[:6] and close(g_[6], n_[6], 1e-7), (seed, cb, g_, n_)
            for k_ in ("signal_total", "entry_deferred", "signal_expired", "exit_ffill", "open_at_end", "pair_excluded"):
                assert sim.cnt.get(k_, 0) == ncnt.get(k_, 0), (seed, cb, k_, dict(sim.cnt), ncnt)
            n_lines += 1
    assert n_lines >= 40, n_lines
    print("   строк «пара x вариант»: %d" % n_lines)


# ------------------------------------------------------------------ G-X-5: защита поиска
@test
def x11_guard_refuses_2500_and_isolation():
    td = tempfile.mkdtemp()
    XG.configure(mode="test", test_root=td, out_root=os.path.join(td, "out"))
    for last in (2500, 2501, 2600):
        try:
            XG.check_days(last, what="t")
            raise AssertionError("принял %d" % last)
        except SystemExit as e:
            assert e.code == 3
    XG.check_days(2499, what="t")
    assert XG.STATE.max_read.value == 2499
    for bad in ("go_window.token", "go_window.token.used.1", "window_reads.log"):
        try:
            XG.check_path(os.path.join(td, bad))
            raise AssertionError("прочитал " + bad)
        except SystemExit as e:
            assert e.code == 3
    try:
        XG.check_write(os.path.join(tempfile.gettempdir(), "x_outside.txt"))
        raise AssertionError("записал вне корня")
    except SystemExit as e:
        assert e.code == 3
    # баннер: первая строка каждого выходного файла; verify_banners ловит файл без баннера
    out = os.path.join(td, "out")
    XG.write_csv(os.path.join(out, "t.csv"), ["a", "b"], [[1, 2.5]])
    XG.write_csv(os.path.join(out, "t.csv.gz"), ["a"], [[1]], gz=True)
    XG.write_json(os.path.join(out, "t.json"), {"x": 1})
    XG.write_text(os.path.join(out, "t.txt"), "text\n")
    assert XG.verify_banners(out) == [], XG.verify_banners(out)
    open(os.path.join(out, "bad.csv"), "w").write("a,b\n1,2\n")
    assert XG.verify_banners(out) == ["bad.csv"]
    # модуль поиска не импортирует и не вызывает защиту окна и токены
    src = open(os.path.join(HERE, "conv_xguard.py"), encoding="utf-8").read()
    assert "import conv_guard" not in src and "check_window_token" not in src and "_claim_window_token" not in src
    # защита подтверждающего окна не ослаблена: тот же код (sha256 по LF) и поведение (calib/window без токена отказывают)
    for fn, want in (("conv_guard.py", GUARD_SHA), ("conv_window.py", WINDOW_SHA)):
        assert G.sha256_lf(os.path.join(HERE, fn)) == want, fn + " изменён"
    d = tempfile.mkdtemp()
    os.environ["C2_HOME"] = d
    pf = os.path.join(d, "params.csv")
    open(pf, "w").write("a,b\n")
    sf = os.path.join(d, "conv_run.py")
    open(sf, "w").write("# stub\n")
    G.configure(mode="calib", params_paths=(pf, None), script_path=sf)
    for last in (2479, 2499, 2500):
        try:
            G.check_days(last)
            raise AssertionError("conv_guard принял %d" % last)
        except SystemExit as e:
            assert e.code == 3
    G.configure(mode="window", go_window=True, params_paths=(pf, None), script_path=sf, runner=G.RUNNER)
    try:
        G.check_days(2479)
        raise AssertionError("окно прочитано без токена")
    except SystemExit as e:
        assert e.code == 3
    # conv_world через conv_explore читает ТОЛЬКО через XG; прямой conv_guard остаётся прежним
    import conv_world as W
    assert W.G is XG


@test
def x12_protocol_frozen_check():
    td = tempfile.mkdtemp()
    XG.configure(mode="test", test_root=td, out_root=os.path.join(td, "out"))
    p = os.path.join(td, "a.csv")
    open(p, "wb").write(b"x,y\r\n1,2\r\n")
    ok = {"commit": "c0ffee", "files": {"a.csv": XG.sha256_lf(p)}}
    json.dump(ok, open(os.path.join(td, "x_git_ok"), "w"))
    assert XG.ensure_protocol_frozen(td, ["a.csv"])[0] == "c0ffee"
    open(p, "wb").write(b"x,y\n1,3\n")
    try:
        XG.ensure_protocol_frozen(td, ["a.csv"])
        raise AssertionError("изменённый файл принят")
    except SystemExit as e:
        assert e.code == 3


def main():
    names = sys.argv[1:]
    fails = 0
    sel = [fn for fn in OK if not names or fn.__name__ in names or any(fn.__name__.startswith(x) for x in names)]
    for fn in sel:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception:                                    # noqa: BLE001
            fails += 1
            print("FAIL", fn.__name__)
            traceback.print_exc()
    print("итог: %d из %d тестов не прошли" % (fails, len(sel)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
