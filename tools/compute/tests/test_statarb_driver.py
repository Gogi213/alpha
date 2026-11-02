"""TK-484: драйвер счёта П-32 (statarb/driver.py) и сменное формирование (statarb/formation.py)."""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "statarb"))
import driver as d  # noqa: E402
import formation as fm  # noqa: E402

UNIV = (
    "exchange,symbol,from_day,to_day,last_candle_day,delisted,closing_day\n"
    "binance-um,AAAUSDT,2021-01-01,2021-03-31,2026-11-01,0,\n"
    "binance-um,BBBUSDT,2021-01-01,2021-02-10,2021-02-10,1,2021-02-10\n"
    "binance-um,BTCUSDT,2020-06-01,2026-11-01,2026-11-01,0,\n"
    "bybit-linear,AAAUSDT,2021-01-01,2021-03-31,2026-11-01,0,\n"
)
PARAMS = ["window=240", "min_obs=120", "min_span=1440", "entry_z=2", "exit_z=0.5", "stop_z=inf", "max_hold=480",
          "refresh=60", "threads=1"]


def write(dirname, name, text):
    p = os.path.join(dirname, name)
    with open(p, "w", newline="") as f:
        f.write(text)
    return p


class Calendar(unittest.TestCase):
    def test_day_of_and_months(self):
        self.assertEqual(d.day_of("2020-01-01"), 0)
        self.assertEqual(d.day_of("2021-01-01"), 366)
        m = d.months("2020-12", "2021-02")
        self.assertEqual([x[0] for x in m], ["2020-12", "2021-01", "2021-02"])
        self.assertEqual(m[0][2], m[1][1])  # границы смыкаются, to не включая
        self.assertEqual(m[1][2] - m[1][1], 31)

    def test_alive_in_keeps_delisted_until_closing_day(self):
        iv = [("BBB", d.day_of("2021-01-01"), d.day_of("2021-02-10"))]
        self.assertIn("BBB", d.alive_in(iv, d.day_of("2021-02-01"), d.day_of("2021-03-01")))
        self.assertNotIn("BBB", d.alive_in(iv, d.day_of("2021-02-11"), d.day_of("2021-03-01")))


class Formation(unittest.TestCase):
    def test_anchor_excludes_anchor_and_requires_it_alive(self):
        ctx = dict(alive={"BTCUSDT", "AAAUSDT", "BBBUSDT"}, options={"anchor": "BTCUSDT"})
        self.assertEqual(fm.anchor_leg(ctx), [("AAAUSDT", ["BTCUSDT"]), ("BBBUSDT", ["BTCUSDT"])])
        self.assertEqual(fm.anchor_leg(dict(alive={"AAAUSDT"}, options={"anchor": "BTCUSDT"})), [])

    def test_same_coin_uses_only_accepted_alive_rows(self):
        with tempfile.TemporaryDirectory() as t:
            sm = write(t, "sm.csv", "base,exch_a,sym_a,exch_b,sym_b,accepted\n"
                       "A,binance-um,AAAUSDT,bybit-linear,AAAUSDT,1\n"
                       "B,binance-um,BBBUSDT,bybit-linear,BBBUSDT,1\n"
                       "C,binance-um,CCCUSDT,bybit-linear,CCCUSDT,0\n")
            ctx = dict(alive_all={"binance-um": {"AAAUSDT", "BBBUSDT", "CCCUSDT"}, "bybit-linear": {"AAAUSDT", "CCCUSDT"}},
                       options={"symmap": sm, "exch_a": "binance-um", "exch_b": "bybit-linear"})
            self.assertEqual(fm.same_coin(ctx), [("AAAUSDT", ["AAAUSDT"])])

    def test_unknown_formation_is_refused(self):
        with self.assertRaises(SystemExit):
            fm.get("nope")

    def test_stubs_are_marked(self):
        self.assertTrue(fm.fixed_pairs.stub and fm.anchor_leg.stub)
        self.assertFalse(getattr(fm.same_coin, "stub", False))


class Plan(unittest.TestCase):
    def run_plan(self, t, extra, params=PARAMS):
        u = write(t, "universe.csv", UNIV)
        args = ["plan", "--universe", u, "--first-month", "2021-01", "--last-month", "2021-02", "--bin", "/x/alpha",
                "--panels-root", "/p", "--out-dir", os.path.join(t, "out")]
        for p in params:
            args += ["--param", p]
        d.main(args + extra)
        with open(os.path.join(t, "out", "manifest.json")) as f:
            return json.load(f)

    def test_params_have_no_defaults(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(SystemExit) as e:
                self.run_plan(t, ["--waves", "1"], params=PARAMS[:-1])
            self.assertIn("threads", str(e.exception))

    def test_wave1_a2_jobs_per_month_and_stub_flag(self):
        with tempfile.TemporaryDirectory() as t:
            m = self.run_plan(t, ["--waves", "1", "--only", "A2-binance-um", "--formation-opt", "anchor=BTCUSDT"])
            c = m["cells"][0]
            self.assertEqual((c["cell"], c["status"], c["stub"], c["months"]), ("A2-binance-um", "planned", True, 2))
            with open(c["jobs_file"]) as f:
                rows = f.read().splitlines()
            # январь: AAA, BBB; февраль: AAA, BBB (снята 10.02 — жива в феврале)
            self.assertEqual(len(rows) - 1, 4)
            self.assertTrue(rows[1].startswith("AAAUSDT,BTCUSDT,"))

    def test_waves_order_and_missing_pieces_are_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as t:
            m = self.run_plan(t, ["--waves", "1,2,3,4"])
            order = [c["wave"] for c in m["cells"]]
            self.assertEqual(order, sorted(order))
            st = {c["cell"]: c["status"] for c in m["cells"]}
            self.assertEqual(st["A2-binance-um"], "no_option")
            self.assertEqual(st["B1-binance-um-K3"], "no_formation")
            self.assertEqual(st["A1-gate-futures_usdt-bols"], "no_formation")
            self.assertEqual(st["C1-binance-um-bybit-linear"], "blocked")
            self.assertEqual(len(st), 1 + 3 + 6 + 6 + 3)

    def test_run_script_is_resumable_and_command_carries_cell_params(self):
        with tempfile.TemporaryDirectory() as t:
            self.run_plan(t, ["--waves", "1", "--only", "A2-binance-um", "--formation-opt", "anchor=BTCUSDT",
                              "--trade-jobs", "0", "2"])
            with open(os.path.join(t, "out", "run-waves.sh")) as f:
                sh = f.read()
            self.assertIn("[ -e ", sh)
            for tok in ("--kind pair", "--cell A2-binance-um", "--window 240", "--stop-z inf", "--trade-jobs 0,2",
                        "--panels /p/panels-binance-um"):
                self.assertIn(tok, sh)
            self.assertNotIn("--beta-fixed", sh)  # A2 — OLS

    def test_beta1_cell_fixes_beta(self):
        with tempfile.TemporaryDirectory() as t:
            m = self.run_plan(t, ["--waves", "3", "--only", "A1-binance-um-b1", "--cell-formation", "A1-binance-um-b1=anchor",
                                  "--formation-opt", "anchor=BTCUSDT"])
            self.assertEqual(m["cells"][0]["status"], "planned")
            with open(os.path.join(t, "out", "run-waves.sh")) as f:
                self.assertIn("--beta-fixed 1", f.read())


if __name__ == "__main__":
    unittest.main()
