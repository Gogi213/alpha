"""Юнит-тесты П-42 §13.25 (TK-600, ворота G-T1 / G-T3 / G-P): определения TOX13 — ручные значения, независимая медленная реализация, отсутствие заглядывания.
Запуск на srv: cd /data/p42-tk600/py && python3 -m unittest test_tk600 (numpy, pandas)."""
import math
import os
import sys
import tempfile
import unittest
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tk594_feat as F4  # noqa: E402
import tk600_feat as F  # noqa: E402

DAY, MIN, HOUR = F.DAY_MS, F.MIN_MS, F.HOUR_MS
SIG = 1_790_000_000_000 // DAY * DAY + 12 * HOUR + 15_000      # полдень UTC + 15 с (середина минуты)
NAN = float("nan")


def tape(recs):
    """recs: (t, price, qty, buy) → T, P, V, B (по времени)."""
    recs = sorted(recs, key=lambda r: r[0])
    T = np.array([r[0] for r in recs], np.int64)
    P = np.array([r[1] for r in recs], float)
    V = np.array([r[1] * r[2] for r in recs], float)
    B = np.array([r[3] for r in recs], bool)
    return T, P, V, B


def feats(recs, sig=SIG, s=1, q99=None, first=None, avail=None):
    T, P, V, B = tape(recs)
    first = int(T[0]) if first is None else first
    avail = (sig // DAY - 1) * DAY if avail is None else avail
    return F.tox_features(T, P, V, B, sig, s, q99, first, avail)


def same(a, b, tol=1e-9):
    if (a != a) and (b != b):
        return True
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def rand_tape(seed, hours=7.0, n=6000, end=SIG + 20_000):
    rng = np.random.default_rng(seed)
    t = np.sort(rng.integers(SIG - int(hours * HOUR), end, n))
    p = 100.0 * np.exp(np.cumsum(rng.normal(0, 3e-4, n)))
    q = np.exp(rng.normal(0, 1.2, n))
    buy = np.zeros(n, bool)
    cur = True
    for i in range(n):                                            # персистентность знака
        if rng.random() < 0.35:
            cur = not cur
        buy[i] = cur
    return [(int(t[i]), float(p[i]), float(q[i]), bool(buy[i])) for i in range(n)]


class HandTests(unittest.TestCase):
    def test_x_feature_list(self):
        self.assertEqual(len(F.X_FEATS), 55)
        self.assertEqual(len(set(F.X_FEATS)), 55)
        self.assertEqual(len(F.TOX), 13)
        self.assertEqual(F.X_FEATS[:12], F.F12)
        self.assertEqual(F.X_FEATS[12:28], F4.FLOW)
        self.assertEqual(F.X_FEATS[28:42], F4.DERIV)
        self.assertEqual(F.X_FEATS[42:], F.TOX)

    def test_sac_alternating_and_nan_rules(self):
        recs = [(SIG - 100_000 + i * 1000, 100.0, 1.0, i % 2 == 0) for i in range(40)]
        f = feats(recs)
        self.assertAlmostEqual(f["sac300"], -39 / 40)
        same_sign = [(SIG - 100_000 + i * 1000, 100.0, 1.0, True) for i in range(40)]
        self.assertTrue(math.isnan(feats(same_sign)["sac300"]))               # знаменатель 0
        self.assertTrue(math.isnan(feats(recs[:29])["sac300"]))               # N = 29 < 30
        self.assertFalse(math.isnan(feats(recs[:30])["sac300"]))

    def test_markout_hand_value(self):
        recs = [(SIG - 120_000 + i * 1000, 100.0 if i < 6 else 101.0, 1.0, True) for i in range(12)]
        f = feats(recs)
        # все записи t < σ − 10 с; P(t_i + 10 с) = 101 для всех i; markout только у шести записей с ценой 100
        self.assertAlmostEqual(f["mk10_300"], (6 * 100.0 * 1e4 * math.log(1.01)) / (6 * 100.0 + 6 * 101.0), places=8)
        # продажа тейкера: знак наоборот
        recs_s = [(t, p, q, False) for t, p, q, _ in recs]
        self.assertAlmostEqual(feats(recs_s)["mk10_300"], -feats(recs)["mk10_300"], places=9)
        # < 10 записей — NaN
        self.assertTrue(math.isnan(feats(recs[:9])["mk10_300"]))

    def test_markout_future_price_never_after_sigma(self):
        # запись за 5 с до σ: t + 10 с > σ — в окно markout (h = 10 с) не входит; записи t < σ − 10 с входят
        recs = [(SIG - 60_000 + i * 1000, 100.0, 1.0, True) for i in range(40)]
        recs_far = recs + [(SIG + 1000, 200.0, 1.0, True)]                    # будущая запись не должна влиять
        self.assertTrue(same(feats(recs)["mk10_300"], feats(recs_far)["mk10_300"]))

    def test_bigimb_and_side(self):
        recs = [(SIG - 1000, 10.0, 300.0, True), (SIG - 2000, 10.0, 100.0, False), (SIG - 3000, 10.0, 5.0, True), (SIG - 400_000, 10.0, 900.0, False)]
        f = feats(recs, q99=1000.0, first=SIG - 5 * DAY)                     # крупные: 3000 (пок), 1000 (прод); 300 с-окно — без записи SIG − 400 с
        self.assertAlmostEqual(f["bigimb300"], (2 * 3000.0 - 4000.0) / 4000.0)
        self.assertAlmostEqual(feats(recs, s=-1, q99=1000.0, first=SIG - 5 * DAY)["bigimb300"], -0.5)
        # в окне 1800 с крупная запись SIG − 400 с (9000 прод.) входит
        self.assertAlmostEqual(f["bigimb1800"], (2 * 3000.0 - 13000.0) / 13000.0)
        self.assertTrue(math.isnan(feats(recs, q99=None, first=SIG - 5 * DAY)["bigimb300"]))
        self.assertTrue(math.isnan(feats(recs, q99=1e9, first=SIG - 5 * DAY)["bigimb300"]))

    def test_kyle_amihud_hand_value(self):
        M = SIG // MIN
        recs, p = [(( M - 31) * MIN + 30_000, 100.0, 10.0, True)], 100.0
        for j in range(30):
            sgn = 1 if j % 3 != 1 else -1                                     # неравные знаки: Q не константа
            p *= math.exp(0.001 * sgn)
            recs.append(((M - 30 + j) * MIN + 30_000, p, 1000.0 / p, sgn > 0))
        f = feats(recs)
        self.assertAlmostEqual(f["kyle30"], 10.0, places=6)                   # λ = 0,01 бп/$, V̄ = 1000 $
        self.assertAlmostEqual(f["amih30"], 10.0 / (1000.0 / 1e6), places=4)
        # неполная минута σ не входит
        f2 = feats(recs + [(M * MIN + 1000, 120.0, 5.0, True)])
        self.assertAlmostEqual(f2["kyle30"], f["kyle30"], places=9)
        self.assertTrue(math.isnan(f["kyle240"]))                             # меньше ⌈240/3⌉ активных / нет цены до окна
        # меньше 10 активных баров из 30 — NaN
        self.assertTrue(math.isnan(feats(recs[:10])["kyle30"]) and math.isnan(feats(recs[:10])["amih30"]))

    def test_vpin_windows(self):
        rng = np.random.default_rng(1)
        recs = [(SIG - 7 * HOUR + int(i * 6.9 * HOUR / 4000), 100.0, float(rng.exponential(2.0)), bool(rng.random() < 0.6)) for i in range(4000)]
        f = feats(recs)
        T, P, V, B = tape(recs)
        for w, key in ((1, "vpin1h"), (6, "vpin6h")):
            lo = next(i for i, t in enumerate(T) if t >= SIG - w * HOUR)
            self.assertTrue(same(f[key], F4.vpin_value(V, B, lo, len(T))))
        # монета моложе окна — NaN
        g = feats(recs, first=SIG - 30 * 60 * 1000)
        self.assertTrue(math.isnan(g["vpin1h"]) and math.isnan(g["vpin6h"]))

    def test_unavailable_history_is_nan(self):
        recs = rand_tape(3)
        f = feats(recs, avail=SIG - 20 * MIN)                                 # данные есть только 20 мин до σ
        for k in ("vpin1h", "vpin6h", "kyle30", "kyle240", "amih30", "amih240", "bigimb1800", "sac1800", "mk60_1800"):
            self.assertTrue(math.isnan(f[k]), k)


class ReferenceTests(unittest.TestCase):
    """Независимая медленная реализация (циклы Python, без общих функций)."""

    @staticmethod
    def ref(recs, sig, s, q99, first, avail):
        rs = [r for r in sorted(recs, key=lambda r: r[0]) if r[0] < sig]
        out = {k: NAN for k in F.TOX}
        if not rs:
            return out

        def price_at(t):
            last = None
            for r in rs:
                if r[0] <= t:
                    last = r[1]
                else:
                    break
            return last
        for w_h, key in ((1, "vpin1h"), (6, "vpin6h")):
            if first <= sig - w_h * HOUR and sig - w_h * HOUR >= avail:
                win = [r for r in rs if r[0] >= sig - w_h * HOUR]
                V = np.array([r[1] * r[2] for r in win])
                B = np.array([r[3] for r in win])
                out[key] = F4.vpin_value(V, B, 0, len(win))
        m = sig // MIN
        for W, kk, ka in ((30, "kyle30", "amih30"), (240, "kyle240", "amih240")):
            if (m - W) * MIN < avail:
                continue
            p0 = price_at((m - W) * MIN - 1)
            if p0 is None:
                continue
            Vk, Qk, ends = [], [], []
            for k in range(m - W, m):
                lo, hi = k * MIN, (k + 1) * MIN
                vs = [(r[1] * r[2], r[3]) for r in rs if lo <= r[0] < hi]
                Vk.append(sum(v for v, _ in vs))
                Qk.append(sum(v if b else -v for v, b in vs))
                ends.append(price_at(hi - 1))
            prices = [p0] + ends
            r_ = [1e4 * math.log(prices[i + 1] / prices[i]) for i in range(W)]
            if sum(1 for v in Vk if v > 0) < math.ceil(W / 3):
                continue
            qm, rm = sum(Qk) / W, sum(r_) / W
            den = sum((q - qm) ** 2 for q in Qk)
            if den > 0:
                out[kk] = sum((q - qm) * (x - rm) for q, x in zip(Qk, r_)) / den * (sum(Vk) / W)
            out[ka] = sum(abs(x) / (v / 1e6) for x, v in zip(r_, Vk) if v > 0) / sum(1 for v in Vk if v > 0)
        for w_s, key in ((300, "bigimb300"), (1800, "bigimb1800")):
            if sig - w_s * 1000 < avail or q99 is None:
                continue
            big = [(r[1] * r[2], r[3]) for r in rs if r[0] >= sig - w_s * 1000 and r[1] * r[2] >= q99]
            vt = sum(v for v, _ in big)
            if vt > 0:
                out[key] = s * (2 * sum(v for v, b in big if b) - vt) / vt
        for w_s, key in ((300, "sac300"), (1800, "sac1800")):
            if sig - w_s * 1000 < avail:
                continue
            d = [1.0 if r[3] else -1.0 for r in rs if r[0] >= sig - w_s * 1000]
            if len(d) >= F.SAC_MIN_N:
                dm = sum(d) / len(d)
                den = sum((x - dm) ** 2 for x in d)
                if den > 0:
                    out[key] = sum((d[i] - dm) * (d[i + 1] - dm) for i in range(len(d) - 1)) / den
        for h_s, w_s, key in ((10, 300, "mk10_300"), (60, 300, "mk60_300"), (60, 1800, "mk60_1800")):
            if sig - w_s * 1000 < avail:
                continue
            win = [r for r in rs if sig - w_s * 1000 <= r[0] and r[0] + h_s * 1000 < sig]
            if len(win) < F.MK_MIN_N:
                continue
            num = den = 0.0
            for r in win:
                v = r[1] * r[2]
                mk = (1.0 if r[3] else -1.0) * 1e4 * math.log(price_at(r[0] + h_s * 1000) / r[1])
                num += v * mk
                den += v
            out[key] = num / den
        return out

    def test_matches_reference_on_random_tapes(self):
        n_cmp = 0
        for seed in range(6):
            recs = rand_tape(seed, n=3000 + 400 * seed)
            q99 = 40.0 if seed % 2 == 0 else None
            for s in (1, -1):
                for sig in (SIG, SIG - 37 * MIN - 123):
                    first = SIG - 3 * DAY
                    avail = SIG - 7 * HOUR + (HOUR if seed == 5 else 0)
                    a = feats(recs, sig=sig, s=s, q99=q99, first=first, avail=avail)
                    b = self.ref(recs, sig, s, q99, first, avail)
                    for k in F.TOX:
                        self.assertTrue(same(a[k], b[k], 1e-9), f"seed {seed} s {s} sig {sig - SIG} {k}: {a[k]} vs {b[k]}")
                        n_cmp += 0 if a[k] != a[k] else 1
        self.assertGreater(n_cmp, 150)                                        # проверено достаточно непустых значений


class NoLookaheadTests(unittest.TestCase):
    def test_future_records_do_not_change_values(self):
        recs = rand_tape(11, end=SIG - 1)
        base = feats(recs, q99=40.0)
        rng = np.random.default_rng(5)
        fut = [(SIG + int(x), float(rng.uniform(50, 150)), float(rng.uniform(1, 100)), bool(rng.random() < 0.5)) for x in rng.integers(0, 600_000, 500)]
        fut.append((SIG, 77.0, 3000.0, True))                                 # запись ровно на σ тоже не входит
        for k, v in feats(recs + fut, q99=40.0).items():
            self.assertTrue(same(v, base[k]), k)

    def test_features_change_when_past_changes(self):
        recs = rand_tape(12, end=SIG - 1)
        base = feats(recs, q99=40.0)
        mod = [(t, p * 1.01 if t > SIG - 100_000 else p, q, b) for t, p, q, b in recs]    # скачок цены в последние 100 с
        ch = feats(mod, q99=40.0)
        self.assertFalse(same(ch["mk10_300"], base["mk10_300"]))
        self.assertFalse(same(ch["kyle30"], base["kyle30"]))


class ReadTests(unittest.TestCase):
    def test_reader_matches_tk594(self):
        with tempfile.TemporaryDirectory() as root:
            day = 20_400
            name = f"AAAUSDT-aggTrades-{F4.date_str(day)}"
            os.makedirs(f"{root}/AAAUSDT")
            rows = ["agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"]
            ts = [day * DAY + 5000, day * DAY + 4000, day * DAY + 9000, day * DAY + 9000]
            for i, (t, pq, mk) in enumerate(zip(ts, [(10.0, 2.0), (11.0, 3.0), (12.0, 1.0), (13.0, 4.0)], ["true", "false", "true", "false"])):
                rows.append(f"{i},{pq[0]},{pq[1]},{i},{i},{t},{mk}")
            with zipfile.ZipFile(f"{root}/AAAUSDT/{name}.zip", "w") as zf:
                zf.writestr(name + ".csv", "\n".join(rows) + "\n")
            old = F4.TRADES
            F4.TRADES = [root]
            try:
                a = F.read_trades_day_p("AAAUSDT", day)
                b = F4.read_trades_day("AAAUSDT", day)
                self.assertIsNone(F.read_trades_day_p("AAAUSDT", day + 1))
            finally:
                F4.TRADES = old
            self.assertTrue(np.array_equal(a[0], b[0]) and np.allclose(a[2], b[1]) and np.array_equal(a[3], b[2]))
            self.assertEqual(list(a[1]), [11.0, 10.0, 12.0, 13.0])           # устойчивая сортировка по времени: 4000, 5000, 9000, 9000

    def test_process_coin_matches_direct_call(self):
        rng = np.random.default_rng(2)
        day = SIG // DAY
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(f"{root}/BBBUSDT")
            tapes = {}
            for d in range(day - 3, day + 1):
                n = 4000
                t = np.sort(rng.integers(d * DAY, (d + 1) * DAY, n))
                p = 100.0 * np.exp(np.cumsum(rng.normal(0, 3e-4, n)))
                q = np.exp(rng.normal(0, 1.0, n))
                b = rng.random(n) < 0.5
                tapes[d] = (t, p, q, b)
                name = f"BBBUSDT-aggTrades-{F4.date_str(d)}"
                rows = ["agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"]
                rows += [f"{i},{float(p[i])!r},{float(q[i])!r},{i},{i},{int(t[i])},{'false' if b[i] else 'true'}" for i in range(n)]
                if d == day - 2:
                    continue                                                    # сутки без файла
                with zipfile.ZipFile(f"{root}/BBBUSDT/{name}.zip", "w") as zf:
                    zf.writestr(name + ".csv", "\n".join(rows) + "\n")
            base = F4.base_ms("2026-09-01")
            old = F4.TRADES
            F4.TRADES = [root]
            try:
                eps = pd.DataFrame({"ep_id": ["a", "b"], "sym": "BBBUSDT", "side": ["long", "short"], "sig_ms": [SIG - base, SIG - base - 3 * HOUR],
                                    "day": [1, 1]})
                _s, res, st = F.process_coin(("BBBUSDT", eps, base))
            finally:
                F4.TRADES = old
            self.assertEqual(len(res), 2)
            prev = tapes[day - 1]
            cur = tapes[day]
            T = np.concatenate([prev[0], cur[0]])
            P = np.concatenate([prev[1], cur[1]])
            V = np.concatenate([prev[1] * prev[2], cur[1] * cur[2]])
            B = np.concatenate([prev[3], cur[3]])
            first = int(tapes[day - 3][0][0])
            for i, (sig, s) in enumerate(((SIG, 1), (SIG - 3 * HOUR, -1))):
                d = F.tox_features(T, P, V, B, sig, s, None, first, (day - 1) * DAY)
                for k in F.TOX:
                    if k.startswith("bigimb"):
                        continue                                                # q99 берётся из гистограммы (≥ 24 ч записей у монеты есть, сутки day−2 пропущены)
                    self.assertTrue(same(float(res.iloc[i][k]), d[k]), f"{k}: {res.iloc[i][k]} vs {d[k]}")
            self.assertEqual(st["rows_on_missing_day"], 0)


class MissingDayTests(unittest.TestCase):
    def test_rows_on_missing_day_are_nan(self):
        day = SIG // DAY
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(f"{root}/CCCUSDT")
            name = f"CCCUSDT-aggTrades-{F4.date_str(day - 1)}"
            rng = np.random.default_rng(4)
            t = np.sort(rng.integers((day - 1) * DAY, day * DAY, 3000))
            rows = ["agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker"]
            rows += [f"{i},100.0,1.0,{i},{i},{int(t[i])},false" for i in range(len(t))]
            with zipfile.ZipFile(f"{root}/CCCUSDT/{name}.zip", "w") as zf:
                zf.writestr(name + ".csv", "\n".join(rows) + "\n")
            base = F4.base_ms("2026-09-01")
            old = F4.TRADES
            F4.TRADES = [root]
            try:
                eps = pd.DataFrame({"ep_id": ["a"], "sym": "CCCUSDT", "side": ["long"], "sig_ms": [SIG - base], "day": [1]})
                _s, res, st = F.process_coin(("CCCUSDT", eps, base))
            finally:
                F4.TRADES = old
            self.assertTrue(res[F.TOX].isna().all(axis=None))
            self.assertEqual(st["rows_on_missing_day"], 1)


if __name__ == "__main__":
    unittest.main()
