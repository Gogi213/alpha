"""Сверка инкрементального счёта (arb_inc) с полным (core.analyze_pair + arb_live.hook) на синтетических потоках: те же числа до последнего знака."""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze  # noqa: E402
import arb_inc  # noqa: E402
import arb_live  # noqa: E402
import core  # noqa: E402

MIN = 60_000
T_BEGIN = (1_794_000_000_000 // 86_400_000 + 1) * 86_400_000 + 600_000     # 00:10 UTC: три часа данных не переходят границу суток
MARGIN = 60_000


def make_symbol(rng, n, base, basis_fn, gap=None, lag_max=5000, dup=0.2, bad=0.002, tick=0.01):
    """Поток записей одного символа в порядке приёма: dict e, rx, bid, ask, dep (как после загрузки этапа A)."""
    t = np.sort(rng.integers(T_BEGIN, T_BEGIN + 3 * 3600_000, n))
    if gap is not None:
        t = t[(t < gap[0]) | (t > gap[1])]
    n = len(t)
    walk = base + np.cumsum(rng.normal(0, 0.004, n))
    mid = np.round((walk + basis_fn(t)) / tick) * tick
    half = tick * rng.integers(1, 3, n)
    bid, ask = mid - half, mid + half
    rep = rng.random(n) < dup
    for i in np.flatnonzero(rep):                         # повтор предыдущей цены
        if i:
            bid[i], ask[i] = bid[i - 1], ask[i - 1]
    wrong = rng.random(n) < bad
    bid[wrong], ask[wrong] = ask[wrong], bid[wrong]       # bid > ask — негодная запись
    lag = rng.integers(20, lag_max, n)
    e = t
    rx = e + lag
    o = np.argsort(rx, kind="stable")
    return {"e": e[o].astype(np.int64), "rx": rx[o].astype(np.int64), "bid": bid[o], "ask": ask[o], "dep": rng.uniform(100, 5000, n)[o]}


def shocks(rng, k, amp):
    starts = np.sort(rng.integers(T_BEGIN + 70 * MIN, T_BEGIN + 3 * 3600_000, k))
    return [(s, s + int(rng.integers(30_000, 25 * MIN)), rng.choice([-1, 1]) * amp) for s in starts]


def basis_of(sh):
    def f(t):
        b = np.zeros(len(t))
        for s, e, a in sh:
            b[(t >= s) & (t < e)] += a
        return b
    return f


class _All:
    def __contains__(self, k):
        return True


ALL = _All()


def alive_minutes(clean, t1):
    """Минуты, в которые у биржи есть хоть одна запись до t1 (по очищенному потоку) -> множество номеров минут."""
    e = clean["e"][clean["e"] < t1]
    return set(np.unique(e // 60000).tolist())


def ref_chart(A, B, t0, t1, alive_a, alive_b):
    """Эталон графика «как было», напрямую по определению: ряды пары (core.pair_series), для каждой минуты — максимум Sell и минимум Buy
    состояния за минуту (с состоянием на входе), центр минуты — медиана середин закрытий предыдущих 60 минут со строками (≥ 30);
    слот — по живым минутам обеих бирж. Простые циклы, без переноса и плотных массивов."""
    T, S, Bu, _, _ = core.pair_series(A, B, t1)
    tmin0, n = t0 // 60000, int(np.ceil((t1 - t0) / (core.SLOT_MIN * 60000)))
    nan = float("nan")
    hi, lo, mid = {}, {}, {}
    if len(T):
        ps_, pb_ = None, None
        for k in range(int(T[0] // 60000), t1 // 60000):
            sel = (T >= k * 60000) & (T < (k + 1) * 60000)
            cs = ([ps_] if ps_ is not None else []) + list(S[sel])
            cb = ([pb_] if pb_ is not None else []) + list(Bu[sel])
            hi[k], lo[k] = max(cs), min(cb)
            if sel.any():
                ps_, pb_ = S[sel][-1], Bu[sel][-1]
                mid[k] = (ps_ + pb_) / 2
    def centre(k):
        v = [mid[j] for j in range(k - core.WIN_MIN, k) if j in mid]
        return float(np.median(v)) if len(v) >= core.NEED_BARS else nan
    H, L, C = [], [], []
    for j in range(n):
        ks = [k for k in range(tmin0 + j * core.SLOT_MIN, tmin0 + (j + 1) * core.SLOT_MIN) if k in hi and k in alive_a and k in alive_b]
        H.append(max(hi[k] for k in ks) if ks else nan)
        L.append(min(lo[k] for k in ks) if ks else nan)
        C.append(centre(ks[-1]) if ks else nan)
    return [arb_inc.q10(np.array(x)) for x in (H, L, C)]


def full_result(A_raw, B_raw, start, t1, alive=None):
    """Эталон: таблица — полный счёт (clean + analyze_pair + hook) по всем записям, пришедшим к моменту прохода; график — ref_chart.
    alive — (alive_a, alive_b) множества живых минут; по умолчанию все минуты живые."""
    A, B = core.clean(dict(A_raw)), core.clean(dict(B_raw))
    r = core.analyze_pair(A, B, start, t1)
    if r is None:
        return None
    analyze.G.update({"t0": start, "t1": t1})
    d = arb_live.hook(*r)
    if d is None:
        return None
    aa, ab = alive if alive is not None else (ALL, ALL)
    d["S"], d["B"], d["C"] = ref_chart(A, B, start, t1, aa, ab)
    return d


def drive(A_raw, B_raw, start, pass_times, compare=True, alive_fn=None, ref_alive=None):
    """Гонит проходы; после каждого сверяет с полным счётом (если compare). -> (число проходов, число сверок).
    alive_fn(минуты) -> bool-массив живых минут (по умолчанию все); ref_alive — те же минуты множествами (alive_a, alive_b) для эталона."""
    sa, sb, ps = arb_inc.Sym(), arb_inc.Sym(), arb_inc.Pair()
    ia = ib = 0
    t_a = T_BEGIN - 10 * 24 * 3600_000
    npass = nchk = 0
    for P in pass_times:
        na = int(np.searchsorted(A_raw["rx"], P, "right"))
        nb = int(np.searchsorted(B_raw["rx"], P, "right"))
        for s, raw, lo, hi in ((sa, A_raw, ia, na), (sb, B_raw, ib, nb)):
            if hi > lo:
                s.ingest(*(raw[k][lo:hi] for k in ("e", "rx", "bid", "ask", "dep")))
        ia, ib = na, nb
        t_b = (P - MARGIN) // MIN * MIN
        if t_b <= t_a:
            continue
        carry_a, carry_b = sa.carry, sb.carry
        ba, bb = sa.take(t_a, t_b), sb.take(t_a, t_b)
        A, B = arb_inc.with_carry(carry_a, ba), arb_inc.with_carry(carry_b, bb)
        arb_inc.advance_pair(ps, A, B, start, t_b // 60000)
        arb_inc.prune(ps, t_b)
        t_a = t_b
        npass += 1
        if compare:
            t0 = max(start, t_b - arb_live.WINDOW_MS)
            ok = None
            if ps.m0 is not None and alive_fn is not None:
                ok = alive_fn(ps.m0 + np.arange(len(ps.mid)))
            got = arb_inc.result(ps, t0, t_b, arb_live.PERIODS_H, ok)
            want = full_result({k: v[:na] for k, v in A_raw.items()}, {k: v[:nb] for k, v in B_raw.items()}, t0, t_b, ref_alive)
            assert_same(got, want, P)
            nchk += 1
    assert sa.late == 0 and sb.late == 0
    return npass, nchk


def assert_same(got, want, ctx=None):
    if want is None:
        assert got is None, ctx
        return
    assert got is not None, ctx
    assert got["cnt"] == want["cnt"], (ctx, got["cnt"], want["cnt"])
    assert got["big"] == want["big"], (ctx, got["big"], want["big"])
    for k in ("n", "pk", "tau", "net"):
        assert got["st"][k] == want["st"][k], (ctx, k, got["st"][k], want["st"][k])
    for k in ("S", "B", "C"):
        assert got[k] == want[k], (ctx, k)


def test_big_counts_by_start_and_peak():
    t1, day = 10 * 86_400_000, 86_400_000
    ep = lambda start, peak: (start, 1, 1000, False, peak, 5.0, 1.0)  # noqa: E731
    rows = [ep(t1 - day, 30.0), ep(t1 - day - 1, 200.0), ep(t1 - 1000, 49.9), ep(t1 - 5000, 50.0), ep(t1 - 7000, 100.0), ep(t1, 12.6)]
    assert core.big_counts(rows, t1) == [4, 2, 1]      # начало ≥ t1 − 24 ч; пик ≥ 30 / 50 / 100 bps включительно
    assert core.big_counts([], t1) == [0, 0, 0]


def scenario(seed, n=9000, k_shocks=9, gap=None, every=(1, 7)):
    rng = np.random.default_rng(seed)
    sh = shocks(rng, k_shocks, 0.25)
    A = make_symbol(rng, n, 100.0, lambda t: np.zeros(len(t)), gap=gap)
    B = make_symbol(rng, n, 100.0, basis_of(sh), gap=None)
    B["bid"] -= 0.0                                  # базис задан в basis_fn (через mid)
    # потоки уже включают базис у B: перенос базиса у A не нужен — разница базисов и есть расхождение
    t, times = T_BEGIN + 66 * MIN, []
    while t < T_BEGIN + 3 * 3600_000 + 6 * MIN:
        t += int(rng.integers(*every)) * MIN + int(rng.integers(0, 40_000))
        times.append(t)
    return A, B, T_BEGIN + 70 * MIN, np.array(times, np.int64)


def test_incremental_equals_full_every_pass():
    A, B, start, times = scenario(1)
    npass, nchk = drive(A, B, start, times)
    assert npass > 20 and nchk == npass


def test_other_seeds_and_pass_cadence():
    for seed, every in ((2, (1, 2)), (3, (5, 6)), (4, (1, 15))):
        A, B, start, times = scenario(seed, every=every)
        drive(A, B, start, times)


def test_many_episodes_open_across_passes():
    for seed in range(10, 14):
        A, B, start, times = scenario(seed, k_shocks=30)
        drive(A, B, start, times)


def test_rare_symbol_with_long_gap_uses_carry():
    A, B, start, times = scenario(5, n=3000, gap=(T_BEGIN + 80 * MIN, T_BEGIN + 140 * MIN))
    drive(A, B, start, times)


def test_one_shot_bootstrap_equals_many_passes():
    A, B, start, times = scenario(6)
    drive(A, B, start, times[-1:], compare=True)           # один проход = весь счёт сразу
    drive(A, B, start, times, compare=False)


def test_late_record_beyond_boundary_is_dropped_and_counted():
    s = arb_inc.Sym()
    s.ingest(np.array([100, 200], np.int64), np.array([110, 210], np.int64), np.array([1.0, 1.1]), np.array([1.2, 1.3]), np.array([5.0, 5.0]))
    assert len(s.take(0, 150)["e"]) == 1
    s.ingest(np.array([120], np.int64), np.array([300], np.int64), np.array([1.0]), np.array([1.2]), np.array([5.0]))
    b = s.take(150, 400)
    assert s.late == 1 and b["e"].tolist() == [200]


def final_chart(A, B, start, times, **kw):
    """Гонит все проходы со сверкой и возвращает график последнего состояния (S, B, C)."""
    sa, sb, ps = arb_inc.Sym(), arb_inc.Sym(), arb_inc.Pair()
    na, nb = int(np.searchsorted(A["rx"], times[-1], "right")), int(np.searchsorted(B["rx"], times[-1], "right"))
    sa.ingest(*(A[k][:na] for k in ("e", "rx", "bid", "ask", "dep")))
    sb.ingest(*(B[k][:nb] for k in ("e", "rx", "bid", "ask", "dep")))
    t_b = (times[-1] - MARGIN) // MIN * MIN
    A_, B_ = arb_inc.with_carry(None, sa.take(t_b - 10 ** 12, t_b)), arb_inc.with_carry(None, sb.take(t_b - 10 ** 12, t_b))
    arb_inc.advance_pair(ps, A_, B_, T_BEGIN + 70 * MIN, t_b // 60000)
    alive = kw.get("alive_fn")
    ok = None if alive is None else alive(ps.m0 + np.arange(len(ps.mid)))
    return arb_inc.result(ps, T_BEGIN + 70 * MIN, t_b, arb_live.PERIODS_H, ok), t_b


class _Gap:
    def __init__(self, k0, k1):
        self.k0, self.k1 = k0, k1

    def __contains__(self, k):
        return not self.k0 <= k < self.k1


def test_illiquid_pair_chart_has_no_holes():
    """Владелец 15.11: «дырки в графике» (GE Bybit→BingX): у обеих сторон мало записей — слоты без записей не должны быть пустыми."""
    rng = np.random.default_rng(21)
    A = make_symbol(rng, 150, 100.0, lambda t: np.zeros(len(t)), dup=0.0, bad=0.0)       # ~50 записей в час
    B = make_symbol(rng, 150, 100.0, basis_of(shocks(rng, 3, 0.25)), dup=0.0, bad=0.0)
    start = T_BEGIN + 70 * MIN
    times = np.array([T_BEGIN + 3 * 3600_000 + 5 * MIN], np.int64)
    got, t_b = final_chart(A, B, start, times)
    assert got is not None
    for k in ("S", "B"):
        v = got[k]
        first = next(i for i, x in enumerate(v) if x is not None)
        assert all(x is not None for x in v[first:]), (k, [i for i, x in enumerate(v) if x is None][:10])
    ca, cb = core.clean(dict(A)), core.clean(dict(B))
    old = core.chart_slots(core.analyze_pair(ca, cb, start, t_b)[1], start // 60000, len(got["S"]))
    assert sum(x is None for x in old[0][old[0].index(next(x for x in old[0] if x is not None)):]) > 0    # прежняя выборка давала дыры
    assert_same(got, full_result(A, B, start, t_b), "illiquid")                           # и совпадает с эталоном по определению


def test_collector_gap_breaks_the_line_only_there():
    A, B, start, times = scenario(31)
    g0, g1 = (T_BEGIN + 100 * MIN) // MIN, (T_BEGIN + 118 * MIN) // MIN
    drive(A, B, start, times[::3], alive_fn=lambda m: ~((m >= g0) & (m < g1)), ref_alive=(_Gap(g0, g1), ALL))
    got, t_b = final_chart(A, B, start, times, alive_fn=lambda m: ~((m >= g0) & (m < g1)))
    tmin0 = start // 60000
    for j, v in enumerate(got["S"]):
        ks = range(tmin0 + 3 * j, tmin0 + 3 * j + 3)
        if all(g0 <= k < g1 for k in ks):
            assert v is None and got["B"][j] is None, j             # слот целиком в провале — линия рвётся
        elif tmin0 + 3 * j >= (T_BEGIN + 75 * MIN) // MIN and not any(g0 <= k < g1 for k in ks):
            assert v is not None, j                                  # вне провала дыр нет
