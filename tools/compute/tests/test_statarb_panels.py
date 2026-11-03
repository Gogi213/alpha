"""TK-487: панели .sap по вселенной П-32 — панели/манифест/оценка места/сверка (statarb/panels_manifest.py, verify_panels.py)."""
import csv, gzip, importlib.util, os, re, shutil, struct, subprocess, sys, tempfile, unittest
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
STAT = os.path.join(HERE, "..", "statarb")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(STAT, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


pm = load("panels_manifest")
vp = load("verify_panels")
NAN = float("nan")
G0 = 1_577_836_800_000
MIN = 60_000


def write_sap(path, first, op, cl):
    with open(path, "wb") as f:
        f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first, len(cl)))
        f.write(array("d", op).tobytes() + array("d", cl).tobytes())


def write_csv(path, rows, header=True, eol="\n"):
    """rows: (минута, o, c, v) → csv свечей (h, l — произвольные); .gz по расширению."""
    lines = (["t_open_ms,o,h,l,c,v,quote_v,n_trades"] if header else []) + [
        "%d,%r,%r,%r,%r,%r,1,1" % (G0 + m * MIN, o, max(o, c), min(o, c), c, v) for m, o, c, v in rows]
    data = eol.join(lines) + eol
    if path.endswith(".gz"):
        with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
            f.write(data)
    else:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(data)


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def p(self, *a):
        return os.path.join(self.d, *a)


class Header(TmpCase):
    def test_days_and_bytes(self):
        write_sap(self.p("A.sap"), 1440, [1.0] * 2880, [2.0] * 2880)
        self.assertEqual(pm.read_header(self.p("A.sap")), (1440, 2880))
        rows = pm.manifest_rows("ex", self.d)
        self.assertEqual(rows, [["ex", "A", "2020-01-02", "2020-01-03", 2880, 24 + 16 * 2880]])

    def test_single_minute_last_day_is_first_day(self):
        write_sap(self.p("B.sap"), 5, [1.0], [1.0])
        self.assertEqual(pm.manifest_rows("ex", self.d)[0][2:4], ["2020-01-01", "2020-01-01"])

    def test_zero_minutes_row_not_dropped(self):
        write_sap(self.p("Z.sap"), 0, [], [])
        self.assertEqual(pm.manifest_rows("ex", self.d), [["ex", "Z", "", "", 0, 24]])

    def test_bad_inputs_raise_with_name(self):
        open(self.p("E.sap"), "wb").close()
        with self.assertRaisesRegex(ValueError, "E.sap"):
            pm.read_header(self.p("E.sap"))
        with open(self.p("S.sap"), "wb") as f:
            f.write(b"SAP1" * 3)
        with self.assertRaisesRegex(ValueError, "короче заголовка"):
            pm.read_header(self.p("S.sap"))
        with open(self.p("M.sap"), "wb") as f:
            f.write(b"XXXX" + bytes(20))
        with self.assertRaisesRegex(ValueError, "не панель"):
            pm.read_header(self.p("M.sap"))
        write_sap(self.p("T.sap"), 0, [1.0, 1.0], [1.0, 1.0])
        with open(self.p("T.sap"), "ab") as f:
            f.write(b"\0")
        with self.assertRaisesRegex(ValueError, "размер"):
            pm.read_header(self.p("T.sap"))

    def test_manifest_csv_skips_foreign_names_and_orders(self):
        os.mkdir(self.p("x")); os.mkdir(self.p("y"))
        write_sap(self.p("x", "B.sap"), 0, [1.0], [1.0])
        write_sap(self.p("x", "A.sap"), 0, [1.0], [1.0])
        open(self.p("x", "A.sap.done"), "w").close()
        open(self.p("x", "notes.txt"), "w").close()
        write_sap(self.p("y", "A_USDT.sap"), 0, [1.0], [1.0])
        out = self.p("m.csv")
        counts = pm.write_manifest([("bx", self.p("x")), ("gate", self.p("y"))], out)
        self.assertEqual(counts, {"bx": 2, "gate": 1})
        with open(out, newline="") as f:
            got = [(r["exchange"], r["symbol"]) for r in csv.DictReader(f)]
        self.assertEqual(got, [("bx", "A"), ("bx", "B"), ("gate", "A_USDT")])


class Farm(TmpCase):
    def candles(self):
        os.mkdir(self.p("c"))
        for s, data in [("BTCUSDT", b"x"), ("ETHUSDT", b"x"), ("EMPTY", b"")]:
            with open(self.p("c", s + ".csv.gz"), "wb") as f:
                f.write(data)
        open(self.p("c", "coverage.csv"), "w").close()

    def link(self, syms, **kw):
        try:
            return pm.make_farm(syms, self.p("c"), self.p("farm"))
        except OSError as e:  # Windows без права на симлинки
            self.skipTest("симлинки недоступны: %s" % e)

    def test_union_symbols_missing_and_empty_reported(self):
        self.candles()
        linked, missing = self.link(["BTCUSDT", "NOPEUSDT", "EMPTY"])
        self.assertEqual(linked, ["BTCUSDT"])
        self.assertEqual(missing, ["NOPEUSDT", "EMPTY"])
        self.assertTrue(os.path.islink(self.p("farm", "BTCUSDT.csv.gz")))

    def test_rerun_replaces_link_not_target(self):
        self.candles()
        self.link(["BTCUSDT"])
        self.link(["BTCUSDT"])
        self.assertEqual(open(self.p("c", "BTCUSDT.csv.gz"), "rb").read(), b"x")

    def test_candle_files_ignore_non_gz_and_empty(self):
        self.candles()
        self.assertEqual(sorted(pm.candle_files(self.p("c"))), ["BTCUSDT", "ETHUSDT"])

    def test_union_symbols_strips_and_skips_blank(self):
        with open(self.p("u.csv"), "w", newline="") as f:
            f.write("exchange,symbol,months_pass\r\nex, A_USDT ,1\r\nex,,1\r\nex,B_USDT,2\r\n")
        self.assertEqual(pm.union_symbols(self.p("u.csv")), ["A_USDT", "B_USDT"])


class Estimate(TmpCase):
    def sources(self):
        with open(self.p("s.csv"), "w") as f:
            f.write("exchange,symbol,source,days_with_candle,first_day,last_day\n"
                    "ex,A,rest,10,2024-01-01,2024-01-10\n"
                    "ex,B,rest,0,2024-01-01,2024-01-10\n"
                    "ex,C,rest,5,2024-02-01,2024-02-01\n")

    def test_span_from_sources_zero_days_and_unknown_skipped(self):
        self.sources()
        est = pm.estimate_bytes(["A", "B", "C", "Q"], self.p("s.csv"), "2026-11-02")
        self.assertEqual(est, (24 + 16 * 1440 * 10) + (24 + 16 * 1440 * 1))

    def test_without_sources_full_span(self):
        est = pm.estimate_bytes(["A"], None, "2020-01-10")
        self.assertEqual(est, 24 + 16 * 1440 * 10)

    def test_refuses_over_fraction(self):
        self.sources()
        with open(self.p("u.csv"), "w") as f:
            f.write("exchange,symbol\nex,A\n")
        need = 24 + 16 * 1440 * 10
        old = pm.free_bytes
        try:
            pm.free_bytes = lambda path: int(need / 0.8) + 10
            self.assertEqual(pm.main(["estimate", "--pair", self.p("u.csv") + "," + self.p("s.csv"), "--free-path", self.d,
                                      "--end-day", "2026-11-02"]), 0)
            pm.free_bytes = lambda path: int(need / 0.8) - 10
            self.assertEqual(pm.main(["estimate", "--pair", self.p("u.csv") + "," + self.p("s.csv"), "--free-path", self.d,
                                      "--end-day", "2026-11-02"]), 3)
            pm.free_bytes = lambda path: need  # --full добавляет полный спан сверху
            self.assertEqual(pm.main(["estimate", "--full", "1", "--free-path", self.d, "--end-day", "2020-01-10"]), 3)
        finally:
            pm.free_bytes = old


class Verify(TmpCase):
    def make(self, ext=".csv.gz", eol="\n", header=True):
        # минуты 3..8: 3 торг., 4 объём 0, 5 торг., 6-7 дыра, 8 торг.; минута 1 — объём 0 до начала, 10 — после конца
        rows = [(1, 9.0, 9.0, 0.0), (3, 1.0, 1.5, 2.0), (4, 1.5, 1.5, 0.0), (5, 1.5, 2.5, 1.0), (8, 2.5, 3.0, 4.0), (10, 3.0, 3.0, 0.0)]
        write_csv(self.p("s" + ext), rows, header=header, eol=eol)
        write_sap(self.p("s.sap"), 3, [1.0, NAN, 1.5, NAN, NAN, 2.5], [1.5, NAN, 2.5, NAN, NAN, 3.0])
        return rows

    def run_verify(self, ext=".csv.gz"):
        return vp.verify("S", self.p("s.sap"), self.p("s" + ext))

    def test_match_zero_diffs(self):
        self.make()
        s, diffs = self.run_verify()
        self.assertEqual((s["diffs"], diffs, s["csv_rows"], s["zero_volume"], s["panel_minutes"], s["panel_nan"]),
                         (0, [], 6, 3, 6, 3))

    def test_plain_csv_crlf_without_header(self):
        self.make(ext=".csv", eol="\r\n", header=False)
        self.assertEqual(self.run_verify(".csv")[0]["diffs"], 0)

    def test_changed_value_caught(self):
        self.make()
        write_sap(self.p("s.sap"), 3, [1.0, NAN, 1.5000001, NAN, NAN, 2.5], [1.5, NAN, 2.5, NAN, NAN, 3.0])
        s, diffs = self.run_verify()
        self.assertEqual(s["diffs"], 1)
        self.assertIn("мин 5", diffs[0])

    def test_value_where_volume_zero_caught(self):
        self.make()
        write_sap(self.p("s.sap"), 3, [1.0, 1.5, 1.5, NAN, NAN, 2.5], [1.5, 1.5, 2.5, NAN, NAN, 3.0])
        self.assertEqual(self.run_verify()[0]["diffs"], 1)

    def test_value_in_hole_caught(self):
        self.make()
        write_sap(self.p("s.sap"), 3, [1.0, NAN, 1.5, 7.0, NAN, 2.5], [1.5, NAN, 2.5, 7.0, NAN, 3.0])
        self.assertEqual(self.run_verify()[0]["diffs"], 1)

    def test_span_must_match_first_and_last_nonzero_row(self):
        self.make()
        write_sap(self.p("s.sap"), 2, [NAN, 1.0, NAN, 1.5, NAN, NAN, 2.5], [NAN, 1.5, NAN, 2.5, NAN, NAN, 3.0])
        s, diffs = self.run_verify()
        self.assertGreaterEqual(s["diffs"], 1)
        self.assertTrue(any("первая минута" in d for d in diffs))

    def test_main_exit_codes(self):
        self.make()
        arg = "S=%s,%s" % (self.p("s.sap"), self.p("s.csv.gz"))
        self.assertEqual(vp.main([arg]), 0)
        write_sap(self.p("s.sap"), 3, [1.0, NAN, 9.9, NAN, NAN, 2.5], [1.5, NAN, 2.5, NAN, NAN, 3.0])
        self.assertEqual(vp.main([arg]), 1)
        self.assertEqual(vp.main(["без-двоеточия"]), 2)


class Script(unittest.TestCase):
    def test_bash_syntax(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("нет bash")
        r = subprocess.run([bash, "-n", os.path.join(STAT, "convert_universe.sh")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_script_has_no_deletion_of_raw(self):
        lines = [ln.split("#")[0] for ln in open(os.path.join(STAT, "convert_universe.sh"), encoding="utf-8")]
        self.assertFalse([ln for ln in lines if re.search(r"(^|[;&|(]\s*)(rm|mv|find|du)\s", ln)])


if __name__ == "__main__":
    unittest.main()
