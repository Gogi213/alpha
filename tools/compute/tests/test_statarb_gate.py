"""TK-484: ворота данных П-32 §4 (statarb/gate_data.py) — множители символов, Kneedle, вселенная со снятыми."""
import csv, gzip, importlib.util, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("gate_data", os.path.join(HERE, "..", "statarb", "gate_data.py"))
g = importlib.util.module_from_spec(spec)
sys.modules["gate_data"] = g
spec.loader.exec_module(g)


class SplitSymbol(unittest.TestCase):
    def test_multiplier_prefix(self):
        self.assertEqual(g.split_symbol("1000PEPEUSDT"), ("PEPE", 1000))
        self.assertEqual(g.split_symbol("1000000MOGUSDT"), ("MOG", 1000000))
        self.assertEqual(g.split_symbol("PEPE_USDT"), ("PEPE", 1))

    def test_digit_name_without_zero_is_not_multiplier(self):
        self.assertEqual(g.split_symbol("1INCHUSDT"), ("1INCH", 1))
        self.assertEqual(g.split_symbol("1CAT_USDT"), ("1CAT", 1))


class Kneedle(unittest.TestCase):
    def test_knee_of_plateau_then_drop(self):
        vals = [0.99] * 70 + [0.9, 0.7, 0.5, 0.3, 0.2, 0.1] + [0.05] * 24
        i, v, shape = g.kneedle(vals)
        self.assertEqual(shape, "concave")
        self.assertTrue(66 <= i <= 80, i)

    def test_too_few_or_flat(self):
        self.assertIsNone(g.kneedle([1.0, 1.0, 1.0]))
        self.assertIsNone(g.kneedle([1.0, 0.5]))


class Symmap(unittest.TestCase):
    def daily(self, scale, drift=0.0):
        return {f"2024-01-{d:02d}": scale * (1 + drift) * (1 + 0.01 * d) for d in range(1, 11)}

    def test_pepe_multiplier_accepted_and_mismatch_rejected(self):
        daily = {
            "binance-um": {"1000PEPEUSDT": self.daily(0.008 * 1000), "ABCUSDT": self.daily(5.0)},
            "gate-futures_usdt": {"PEPE_USDT": self.daily(0.008, 0.001), "ABC_USDT": self.daily(7.3)},
        }
        # PEPE: 1000PEPE (8.0) против PEPE (0.008) — отношение ≈ 1000; ABC: 5,0 против 7,3 — не степень 10
        rows = g.build_symmap(daily)
        by = {r["base"]: r for r in rows}
        self.assertEqual(by["PEPE"]["k"], 3)
        self.assertEqual(by["PEPE"]["name_ratio"], 1000.0)
        self.assertLess(by["PEPE"]["dev"], 0.002)
        self.assertGreater(by["ABC"]["dev"], 0.1)
        g.accept_rows(rows, 0.01)
        by = {r["base"]: r for r in rows}
        self.assertEqual((by["PEPE"]["accepted"], by["ABC"]["accepted"]), (1, 0))

    def test_threshold_from_knee_separates_matches(self):
        rows = [dict(dev=d) for d in [0.001, 0.002, 0.0015, 0.003, 0.0008, 0.004, 0.0025, 0.002, 0.5, 0.7, 0.4]]
        thr = g.dev_threshold(rows)
        self.assertIsNotNone(thr)
        g.accept_rows([dict(r, k=0) for r in rows], thr)


class Universe(unittest.TestCase):
    def test_delisted_closes_at_last_candle(self):
        uni = [dict(exchange="e", symbol="AAA", from_day="2024-01-01", to_day="2024-03-01"),
               dict(exchange="e", symbol="BBB", from_day="2024-01-01", to_day="2024-12-31")]
        src = [dict(exchange="e", symbol="AAA", last_day="2024-02-20"),
               dict(exchange="e", symbol="BBB", last_day="2024-12-31")]
        out = g.universe_with_closing(uni, src)
        a = [r for r in out if r["symbol"] == "AAA"][0]
        b = [r for r in out if r["symbol"] == "BBB"][0]
        self.assertEqual((a["delisted"], a["closing_day"]), (1, "2024-02-20"))
        self.assertEqual((b["delisted"], b["closing_day"]), (0, ""))

    def test_gate_knee_uses_universe_only(self):
        cov = [{"биржа": "gate-futures_usdt", "символ": f"S{i}_USDT", "строк": str(1000 - i * 10),
                "ожидаемо_минут": "1000", "ошибка": ""} for i in range(40)]
        cov.append({"биржа": "gate-futures_usdt", "символ": "OUT_USDT", "строк": "1", "ожидаемо_минут": "1000", "ошибка": ""})
        n, kn = g.gate_knee(cov, {f"S{i}_USDT" for i in range(40)})
        self.assertEqual(n, 40)
        self.assertIsNotNone(kn)


class Probe(unittest.TestCase):
    def test_probe_takes_noon_close_only(self):
        d = tempfile.mkdtemp()
        base = 1577836800000  # 2020-01-01 00:00 UTC
        lines = ["t_open_ms,o,h,l,c,v,quote_v,n_trades"]
        for m in range(0, 1440 * 2, 1):
            ts = base + m * 60000
            lines.append(f"{ts},1,1,1,{100 + m},1,1,1")
        with gzip.open(os.path.join(d, "AAAUSDT.csv.gz"), "wt") as f:
            f.write("\n".join(lines) + "\n")
        out = os.path.join(d, "daily.csv")
        g.main(["probe", "--dir", d, "--out", out, "--jobs", "1"])
        rows = list(csv.DictReader(open(out)))
        self.assertEqual([(r["day"], r["close12"]) for r in rows], [("2020-01-01", "820"), ("2020-01-02", "2260")])


if __name__ == "__main__":
    unittest.main()
