"""Тесты §13.32 (TK-625): события пробоев (пересечение, исходы при m·NATR), причинные счётчики на кандидате, валидность окна, таблица --ftab, пороги клеток. Синтетика, без движка, без P&L.
Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk625.py` — выполняет все test_* и печатает итог.
"""
import datetime
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
import tk625_events as ev  # noqa: E402
import tk625_feat as f5  # noqa: E402

SC = 100_000_000
MIN = 60_000
R = 100 * SC                       # уровень 100,0 (e8)
NATR = 1_000_000                   # 1 % (e8)
ONE = 1 * SC                       # 1 NATR от 100 = 1,0


def raises(exc, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc as e:
        return str(e)
    raise AssertionError("ожидалось %s" % exc.__name__)


def ms(date, hh=0, mm=0):
    d = datetime.datetime.fromisoformat(date).replace(tzinfo=datetime.timezone.utc)
    return int(d.timestamp() * 1000) + (hh * 60 + mm) * 60_000


def ticks(*pairs):
    """pairs: (минуты от сигнала, цена) → (T мс от сигнала, P e8)."""
    return np.array([int(m * MIN) for m, _ in pairs], dtype=np.int64), np.array([int(round(p * SC)) for _, p in pairs], dtype=np.int64)


def run(pairs, n_ap=None, natr=NATR, r=R):
    T, P = ticks(*pairs)
    n = int(np.searchsorted(T, 60 * MIN, "right")) if n_ap is None else n_ap
    return ev.event_outcomes(T, P, r, natr, n)


# ---- события ------------------------------------------------------------------------------------------------------------------

def test_no_cross_no_event():
    t, res = run([(1, 99.5), (2, 100.0), (3, 99.9)])           # цена ровно на уровне — не пересечение (строго за уровнем)
    assert t is None and res == {}


def test_success_all_m():
    t, res = run([(1, 99.9), (2, 100.2), (3, 100.6), (4, 101.2), (5, 102.5)])
    assert t == 2 * MIN
    assert res["05"] == ("o", 3 * MIN) and res["10"] == ("o", 4 * MIN) and res["20"] == ("o", 5 * MIN)


def test_success_threshold_is_ceiling_and_inclusive():
    # 0,5 NATR = 0,5 → цена ровно 100,5 достаточна; 100,4999 — нет
    t, res = run([(1, 100.1), (2, 100.4999999), (3, 99.9)])
    assert res["05"][0] == "f"
    t, res = run([(1, 100.1), (2, 100.5), (3, 99.9)])
    assert res["05"] == ("o", 2 * MIN)


def test_fail_returns_to_level_before_m():
    t, res = run([(1, 100.2), (2, 100.4), (3, 99.99), (4, 101.5)])     # вернулась к уровню (ниже) раньше, чем дошла до 1 NATR
    assert res["05"][0] == "f" and res["10"] == ("f", 3 * MIN) and res["20"][0] == "f"


def test_return_means_trade_at_or_below_level():
    t, res = run([(1, 100.2), (2, 100.0), (3, 101.5)])                 # сделка ровно на уровне — возврат
    assert res["10"] == ("f", 2 * MIN)


def test_stall_no_success_no_return():
    t, res = run([(1, 100.2), (30, 100.4), (59, 100.3), (200, 105.0)])  # за 60 мин после пересечения (до 61-й мин) ничего; сделка на 200-й — вне окна исхода
    assert t == MIN and res["10"] == ("s", MIN + 60 * MIN)


def test_cross_only_inside_approach_window():
    T, P = ticks((1, 99.0), (59, 99.5), (61, 100.5), (62, 103.0))
    n_ap = int(np.searchsorted(T, 60 * MIN, "right"))
    t, res = ev.event_outcomes(T, P, R, NATR, n_ap)
    assert t is None                                                     # пересечение на 61-й минуте — вне окна подхода


def test_outcome_window_is_60_min_after_cross():
    t, res = run([(10, 100.2), (70, 101.5)])                             # успех на 70-й: ровно 60 мин после пересечения (включительно)
    assert res["10"] == ("o", 70 * MIN)
    t, res = run([(10, 100.2), (70.001, 101.5)])
    assert res["10"][0] == "s"


def test_short_side_frame():
    # шорт: рама P = −цена, R = −уровень; пробой вниз
    r = -R
    T = np.array([MIN, 2 * MIN, 3 * MIN], dtype=np.int64)
    P = -np.array([int(x * SC) for x in (99.8, 98.9, 98.0)], dtype=np.int64)
    t, res = ev.event_outcomes(T, P, r, NATR, 3)
    assert t == MIN and res["10"][0] == "o" and res["20"][0] == "o"


def test_classification_known_at_resolution_time():
    """Причинность: итог по сделкам, обрезанным моментом исхода, тот же (успех/возврат известны на момент исхода; stall — на cross + 60 мин)."""
    cases = [[(1, 100.2), (2, 101.2), (3, 99.0)], [(1, 100.2), (2, 99.9), (3, 103.0)], [(1, 100.2), (30, 100.4), (59, 100.3), (200, 105.0)]]
    for pairs in cases:
        T, P = ticks(*pairs)
        t, res = ev.event_outcomes(T, P, R, NATR, len(T))
        for name, (code, tres) in res.items():
            k = int(np.searchsorted(T, tres, "right"))
            t2, res2 = ev.event_outcomes(T[:k], P[:k], R, NATR, k)
            assert res2[name] == (code, tres), (pairs, name, res2[name], (code, tres))


# ---- признак -------------------------------------------------------------------------------------------------------------------

def mk_events(rows):
    """rows: (side, sym, cross_ms, {m: (код, t_res)}) → Events с кодом по m."""
    n = len(rows)
    code = {m: np.array([r[3].get(m, ("", -1))[0] for r in rows], dtype=object) for m in f5.MS_NAMES}
    tres = {m: np.array([r[3].get(m, ("", -1))[1] for r in rows], dtype=np.int64) for m in f5.MS_NAMES}
    return f5.Events(np.array([r[0] for r in rows], dtype=object), np.array([r[1] for r in rows], dtype=object), np.array([r[2] - MIN for r in rows], dtype=np.int64),
                     np.ones(n, dtype=np.int8), np.ones(n, dtype=np.int8), np.array([r[2] for r in rows], dtype=np.int64), code, tres)


def hist_for(rows, names=("AAA", "BBB"), days=None):
    e = f5.dedup_crossings(mk_events(rows))
    cd = {s: np.ones(150, dtype=bool) for s in names} if days is None else days
    return f5.History(e.take(e.side == "long"), cd, list(names))


T0 = ms("2026-08-01")


def test_count_closure_boundary_includes_L():
    c = T0 - 3 * 86_400_000
    h = hist_for([("long", "AAA", c, {"10": ("o", c + 10 * MIN)})])
    t_ok = c + 10 * MIN + f5.L_MS                                         # закрытие = исход + 5 мин; граница включена
    assert int(h.cnt[("10", "o")].count(np.array(["AAA"], dtype=object), np.array([t_ok]), 7)[0]) == 1
    assert int(h.cnt[("10", "o")].count(np.array(["AAA"], dtype=object), np.array([t_ok - 1]), 7)[0]) == 0


def test_count_window_and_class_and_coin():
    d = 86_400_000
    rows = [("long", "AAA", T0 - 1 * d, {"10": ("o", T0 - 1 * d + 5 * MIN)}),
            ("long", "AAA", T0 - 6 * d, {"10": ("o", T0 - 6 * d + 5 * MIN)}),
            ("long", "AAA", T0 - 8 * d, {"10": ("o", T0 - 8 * d + 5 * MIN)}),            # вне окна D = 7
            ("long", "AAA", T0 - 2 * d, {"10": ("f", T0 - 2 * d + 5 * MIN)}),
            ("long", "AAA", T0 - 3 * d, {"10": ("s", T0 - 3 * d + 60 * MIN)}),
            ("long", "BBB", T0 - 1 * d, {"10": ("o", T0 - 1 * d + 5 * MIN)})]
    h = hist_for(rows)
    x = np.array(["AAA"], dtype=object), np.array([T0])
    o, f, s = h.counts(x[0], x[1], 7, "10")
    assert (int(o[0]), int(f[0]), int(s[0])) == (2, 1, 1)
    o30, _, _ = h.counts(x[0], x[1], 30, "10")
    assert int(o30[0]) == 3
    ob, _, _ = h.counts(np.array(["BBB"], dtype=object), x[1], 7, "10")
    assert int(ob[0]) == 1
    oc, _, _ = h.counts(np.array(["ZZZ"], dtype=object), x[1], 7, "10")
    assert int(oc[0]) == 0


def test_event_unresolved_at_t_is_not_counted_and_future_does_not_leak():
    c = T0 - 15 * MIN
    h = hist_for([("long", "AAA", c, {"10": ("o", c + 20 * MIN)})])                # исход на T0 + 5 мин, закрытие T0 + 10 мин: на момент T0 не закрыт
    assert int(h.counts(np.array(["AAA"], dtype=object), np.array([T0]), 7, "10")[0][0]) == 0
    assert int(h.counts(np.array(["AAA"], dtype=object), np.array([T0 + 30 * MIN]), 7, "10")[0][0]) == 1


def test_dedup_same_crossing_two_episodes():
    c = T0 - 86_400_000
    e = mk_events([("long", "AAA", c, {"10": ("o", c + 10 * MIN)}), ("long", "AAA", c, {"10": ("o", c + 10 * MIN)}), ("short", "AAA", c, {"10": ("o", c + 10 * MIN)})])
    e.sig[1] -= 100                                                               # второй эпизод — раньше; берётся наименьший sig
    d = f5.dedup_crossings(e)
    assert len(d) == 2 and sorted(d.side.tolist()) == ["long", "short"] and int(d.sig[d.side == "long"][0]) == c - MIN - 100


def test_valid_window_rules():
    pres = np.ones(150, dtype=bool)
    pres[:5] = False                                                               # монета с 22.06 (индекс 5); первые сутки торгов неполные
    pres[60] = False                                                               # пропуск суток 16.08
    h = hist_for([], names=("AAA",), days={"AAA": pres})
    s = np.array(["AAA"], dtype=object)
    v = lambda t, d: bool(h.valid(s, np.array([t]), d)[0])                         # noqa: E731
    assert not v(ms("2026-06-30"), 7)                                              # окно до 24.06 (начало истории) / первых полных суток монеты (≥ 23.06)
    assert v(ms("2026-07-01"), 7)                                                  # окно 24.06–01.07, в истории
    assert not v(ms("2026-07-10"), 30)                                             # окно с 10.06
    assert v(ms("2026-07-24"), 30)                                                 # окно с 24.06
    assert not v(ms("2026-08-18"), 7)                                              # окно содержит пропущенные сутки 16.08
    assert v(ms("2026-08-24"), 7)
    assert not bool(h.valid(np.array(["QQQ"], dtype=object), np.array([ms("2026-07-24")]), 7)[0])


def test_excluded_and_threshold():
    x = np.array([0, 0, 0, 1, 1, 2, 3, 5, np.nan, np.nan])
    assert f5.excluded(x, 1).tolist() == [True, True, True, False, False, False, False, False, False, False]
    assert f5.threshold_for(x, 0.30) == 1                                           # 3 из 8 нулей = 0,375 ≥ 0,30
    assert f5.threshold_for(x, 0.5) == 2                                            # < 2: 5/8 = 0,625 ≥ 0,5; < 1: 0,375
    assert f5.threshold_for(np.array([np.nan, np.nan]), 0.15) is None


def test_ftab_roundtrip_and_columns():
    cols = {c: np.array([1.0, np.nan, 3.0]) for c in f5.COLS}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.csv")
        n = f5.write_ftab(path, np.array(["B", "A", "A"], dtype=object), np.array([5, 7, 7]), cols)
        lines = open(path, encoding="utf-8").read().splitlines()
    assert n == 2                                                                   # ключ (A, 7) один раз
    assert lines[0] == "sym,sig_ms," + ",".join(f5.COLS) and lines[1].startswith("A,7,") and lines[2].startswith("B,5,")
    assert len(f5.COLS) == 9 and f5.col_name("10", 14) == "ok10_d14"


def test_isolation_refuses_late_dates():
    late = f5.Events(np.array(["long"], dtype=object), np.array(["AAA"], dtype=object), np.array([ms("2026-11-14")], dtype=np.int64), np.ones(1, dtype=np.int8), np.ones(1, dtype=np.int8),
                     np.array([-1], dtype=np.int64), {m: np.array([""], dtype=object) for m in f5.MS_NAMES}, {m: np.array([-1], dtype=np.int64) for m in f5.MS_NAMES})
    per = {w.name: late.take(np.zeros(1, dtype=bool)) for w in st.HIST_WINDOWS}
    per["wc"] = late
    raises(st.GuardError, f5.splice_events, per)


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
