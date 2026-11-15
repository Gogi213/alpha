"""Тесты swings.py: ручной пример и сверка с медленным эталоном на случайных рядах."""
import numpy as np

import core
import swings

T0, T1 = 1_000_000, 1_000_000 + 3 * swings.HOUR


def book(e, bid, ask):
    return {"e": np.asarray(e, np.int64), "bid": np.asarray(bid, float), "ask": np.asarray(ask, float)}


def ref_swings(T, S, Bu, N, L, t0, t1, dead=None):
    """Эталон: цикл по событиям. -> список (t_закрытия − t0, длительность, валовый итог, первая сторона)."""
    ev = []
    for i in range(1, len(T)):
        for side, st in ((1, S >= N), (-1, Bu <= -N)):
            if st[i] and not st[i - 1] and T[i] >= t0:
                ct = T[i] + L
                if ct >= t1:
                    continue
                j = max(k for k in range(len(T)) if T[k] <= ct)
                if not st[j]:
                    continue
                if dead is not None and any(ds <= ct and de >= T[i] for ds, de in zip(*dead)):
                    continue
                ev.append((T[i], side, S[j] if side == 1 else Bu[j]))
    ev.sort(key=lambda x: x[0])
    ch = []
    for e in ev:
        if not ch or ch[-1][1] != e[1]:
            ch.append(e)
    out = []
    for a, b in zip(ch[:-1], ch[1:]):
        if dead is not None and any(ds <= b[0] + L and de >= a[0] for ds, de in zip(*dead)):
            continue
        out.append((b[0] + L - t0, b[0] - a[0], a[1] * (a[2] - b[2]), a[1]))
    return out


def series(rng, n):
    T = np.sort(rng.integers(T0 - 600_000, T1, n))
    T = np.unique(T)
    S = rng.normal(0, 0.25, len(T))
    Bu = S + np.abs(rng.normal(0.05, 0.03, len(T)))
    return T, S, Bu


def test_hand_example():
    # B: 100/100 → Sell = bid_A − 100, Buy = ask_A − 100 (в %). Серия A: 0; +0,5 (на 100 мс); −0,4 (держится); Buy −0,2; +0,6.
    e = [T0 + 1000, T0 + 2000, T0 + 2100, T0 + 5000, T0 + 6000, T0 + 6100, T0 + 9000]
    bid = [100.0, 100.5, 100.0, 99.5, 99.0, 99.7, 100.6]
    ask = [100.0, 100.6, 100.0, 99.6, 99.1, 99.8, 100.7]
    T, S, Bu, _, _ = core.pair_series(book(e, bid, ask), book([T0], [100.0], [100.0]), T1)
    S, Bu = S / 100, Bu / 100
    r0 = swings.swings_for(T, S, Bu, S >= 0.3, Bu <= -0.3, 0, T0, T1, None)
    assert r0[0].tolist() == [5000, 9000] and r0[1].tolist() == [3000, 4000]
    assert np.allclose(r0[2], [0.9, 1.0], atol=1e-5) and r0[3].tolist() == [1, -1]
    # L = 300: выброс +0,5 жил 100 мс — не подтверждён; остаётся один круг −0,4 → +0,6
    r3 = swings.swings_for(T, S, Bu, S >= 0.3, Bu <= -0.3, 300, T0, T1, None)
    assert r3[0].tolist() == [9300] and r3[1].tolist() == [4000] and np.allclose(r3[2], [1.0], atol=1e-5) and r3[3].tolist() == [-1]


def test_matches_reference():
    rng = np.random.default_rng(7)
    for seed in range(12):
        rng = np.random.default_rng(seed)
        T, S, Bu = series(rng, 600)
        dead = (np.array([T0 + 400_000, T0 + 4_000_000], np.int64), np.array([T0 + 450_000, T0 + 4_100_000], np.int64)) if seed % 2 else None
        for N in (0.1, 0.3, 0.5):
            for L in (0, 50, 300):
                r = swings.swings_for(T, S, Bu, S >= N, Bu <= -N, L, T0, T1, dead)
                got = sorted((int(a), int(b), float(c), int(d)) for a, b, c, d in zip(*r))
                exp = sorted((int(a), int(b), float(c), int(d)) for a, b, c, d in ref_swings(T, S, Bu, N, L, T0, T1, dead))
                assert len(got) == len(exp) and all(g[:2] == x[:2] and g[3] == x[3] and abs(g[2] - x[2]) < 1e-5 for g, x in zip(got, exp)), (seed, N, L)


def test_hourly_time_with_dead():
    # состояние «за ±N» держится с 10 мин до 100 мин; пропуск 30–40 мин вычитается
    T = np.array([T0 - 1000, T0 + 10 * 60_000, T0 + 100 * 60_000])
    S = np.array([0.0, 0.5, 0.0])
    Bu = np.array([0.05, 0.55, 0.05])
    dead = (np.array([T0 + 30 * 60_000]), np.array([T0 + 40 * 60_000]))
    bey, cov = swings.hourly_time(T, S, Bu, 0.3, T0, 2, dead)
    assert abs(bey[0] - 40 * 60_000) < 1 and abs(bey[1] - 40 * 60_000) < 1   # 10..60 мин минус пропуск 10 мин; 60..100 мин
    assert abs(cov[0] - 50 * 60_000) < 1 and abs(cov[1] - 60 * 60_000) < 1


def test_pair_swings_runs():
    rng = np.random.default_rng(3)
    T, S, Bu = series(rng, 800)
    A = book(T, 100 * (1 + S / 2 / 100), 100 * (1 + S / 2 / 100) + 0.01)
    B = book(T0 - 700_000 + np.arange(1), [100.0], [100.01])
    r = swings.pair_swings(A, B, T0, T1)
    assert r is not None and set(r["sw"]) == {(i, L) for i in range(len(swings.NS)) for L in swings.LS}
    assert r["bey"].shape == (len(swings.NS), 3) and (r["cov"] > 0).all()
