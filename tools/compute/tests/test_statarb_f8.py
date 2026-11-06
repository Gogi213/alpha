"""TK-493 S3: тесты контролей и запуска F8 (f8_shift, f8_lists, f8_randentry, f8_agg, f8_run.sh). Торговое окно не читается: синтетика и закоммиченные списки S1."""
import csv, hashlib, math, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
XD = os.path.abspath(os.path.join(HERE, "..", "statarb", "xdl9"))
sys.path.insert(0, XD)
sys.path.insert(0, os.path.join(XD, ".."))

import f8_shift as SH   # noqa: E402
import f8_agg as AG     # noqa: E402

try:
    import numpy as np
    import f8_randentry as RE
except ImportError:      # numpy нет - тесты симуляции пропускаются
    np = None

NAN = float("nan")


class ShiftTests(unittest.TestCase):
    def test_equals_null_rule_on_formation(self):
        import sel_shift as S
        for mode in ("cal", "trade"):
            for d in range(S.D_FORM0, S.D_TRADE0):
                self.assertEqual(SH.source_day(d, 1, mode), S.source_day(d, 1, mode))

    def test_day_map_calendar_and_trade(self):
        cal = SH.day_map(1, "cal")
        self.assertNotIn(SH.D_FORM0, cal)
        self.assertEqual(cal[2499], 2498)
        tr = SH.day_map(1, "trade")
        # 2480 - пятница, 2481/2482 - суббота/воскресенье, 2483 - понедельник: все берут пятницу 2480; пятница берёт четверг 2479
        self.assertEqual((tr[2481], tr[2482], tr[2483], tr[2480]), (2480, 2480, 2480, 2479))
        self.assertNotIn(2465, tr)    # 01.10 - четверг, раньше источника нет

    def test_shift_panel_blocks_and_nan(self):
        n = 3 * SH.MIN_DAY
        fm = 2465 * SH.MIN_DAY
        import array
        op = array.array("d", [float(i) for i in range(n)])
        cl = array.array("d", [float(i) + 0.5 for i in range(n)])
        nfm, nop, ncl = SH.shift_panel(fm, op, cl, SH.day_map(1, "cal"), 2465, 2468)
        self.assertEqual(nfm, fm)
        self.assertTrue(all(math.isnan(v) for v in nop[:SH.MIN_DAY]))                       # первые сутки без источника
        self.assertEqual(nop[SH.MIN_DAY], 0.0)                                              # сутки 2466 = содержимое суток 2465
        self.assertEqual(ncl[2 * SH.MIN_DAY + 5], cl[SH.MIN_DAY + 5])

    def test_shift_funding_rows_duplicates_friday(self):
        dm = SH.day_map(1, "trade")
        t_fri = SH.GRID0_MS + 2480 * SH.MIN_DAY * 60_000 + 3_600_000     # пятница 01:00
        out = SH.shift_funding_rows([(t_fri, "0.0001,8")], dm)
        days = sorted((t - SH.GRID0_MS) // (SH.MIN_DAY * 60_000) for t, _ in out)
        self.assertEqual(days, [2481, 2482, 2483])                                           # суббота, воскресенье, понедельник
        self.assertTrue(all((t - SH.GRID0_MS) % (SH.MIN_DAY * 60_000) == 3_600_000 for t, _ in out))   # время суток сохранено


class ListsTests(unittest.TestCase):
    def test_manifest_reproduced(self):
        import f8_lists as L
        if not os.path.exists(os.path.join(L.SELDOC, "waves_manifest.csv")):
            self.skipTest("нет манифеста волн")
        n = 0
        for g in L.load_groups():
            cmd, _ = L.unit_main(g, L.MODES["trade"])
            orig = g["cmd"]
            if g["kind"] == "D2":
                import re
                orig = re.sub(r"--pairs \S+\.cells\.csv", "--pairs " + g["jobs_file"], orig)
            self.assertEqual(cmd, orig, g["stem"])
            n += 1
        self.assertEqual(n, len(L.load_groups()))

    def test_no17_list(self):
        import f8_lists as L
        self.assertEqual(len(L.NO17_SYMS), 17)
        self.assertIn("KODEX200USDT", L.NO17_SYMS)


@unittest.skipIf(np is None, "нет numpy")
class RandomEntryTests(unittest.TestCase):
    def test_selftest(self):
        RE.selftest()

    def test_replay_matches_synthetic_trades(self):
        """Сделка, оценённая value_trades, совпадает с ручной формулой ядра для корзины из двух ног X (веса 1/2)."""
        rng = np.random.default_rng(5)
        n = 2 * RE.MIN_DAY
        first = 2465 * RE.MIN_DAY
        mk = lambda p0: p0 * np.exp(np.cumsum(rng.normal(0, 2e-4, n)))
        cy, c1, c2 = mk(10.0), mk(20.0), mk(5.0)
        sh = lambda c: np.concatenate([[c[0]], c[:-1]])
        e = (np.zeros(0, np.int64), np.zeros(0))
        job = RE.Job((first, sh(cy), cy) + e, [(first, sh(c1), c1) + e, (first, sh(c2), c2) + e], 2466, 2467, 500)
        self.assertTrue(job.ok)
        fi, fo = np.array([1000]), np.array([1200])
        g, f, fu = RE.value_trades(job, fi, fi.copy(), fo, np.array([-1.0]), np.array([0.8]))
        q0 = -1.0 / job.oy[1000]
        gross = q0 * (job.oy[1200] - job.oy[1000])
        fee = abs(q0) * (job.oy[1000] + job.oy[1200]) * RE.FEE
        for o in job.ox:
            q = 0.8 * 0.5 / o[1000]
            gross += q * (o[1200] - o[1000])
            fee += q * (o[1000] + o[1200]) * RE.FEE
        self.assertAlmostEqual(g[0], gross * 1e4, places=9)
        self.assertAlmostEqual(f[0], fee, places=9)
        self.assertEqual(fu[0], 0.0)

    def test_seed_reproducible_and_counts(self):
        rng = np.random.default_rng(2)
        n = 2 * RE.MIN_DAY
        first = 2465 * RE.MIN_DAY
        cy = 100 * np.exp(np.cumsum(rng.normal(0, 3e-4, n)))
        cx = 50 * np.exp(np.cumsum(rng.normal(0, 3e-4, n)))
        e = (np.zeros(0, np.int64), np.zeros(0))
        job = RE.Job((first, cy, cy) + e, [(first, cx, cx) + e], 2466, 2467, 300)
        cell = dict(window=300, min_obs=200, beta_fixed=None, max_hold=50)
        r1 = RE.simulate_job(job, cell, 50, np.array([10, 20, 40]), 0, np.random.default_rng([37, 0, 0]), True)
        r2 = RE.simulate_job(job, cell, 50, np.array([10, 20, 40]), 0, np.random.default_rng([37, 0, 0]), True)
        r3 = RE.simulate_job(job, cell, 50, np.array([10, 20, 40]), 0, np.random.default_rng([37, 0, 1]), True)
        self.assertTrue(np.array_equal(r1["fill_in"], r2["fill_in"]))
        self.assertFalse(np.array_equal(r1["fill_in"], r3["fill_in"]))
        self.assertGreaterEqual(len(r1["fill_in"]), 45)                                      # выход возможен почти всегда
        self.assertTrue((r1["fill_out"] - r1["fill_in"] >= 10).all())
        self.assertTrue((r1["fill_in"] >= 2466 * RE.MIN_DAY + 1).all())                     # вход не раньше from_day


class AggTests(unittest.TestCase):
    def _write(self, path, header, rows):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            w.writerows(rows)

    def test_selftest(self):
        AG.selftest()

    def test_cells_and_controls_on_synthetic_run(self):
        tmp = tempfile.mkdtemp()
        try:
            hdr = ["cell", "job", "y", "x", "day", "net_bps", "gross_bps", "fee_bps", "funding_bps", "trades", "active"]
            cell = "A2|aster|crypto|a0"
            rows, rows_pl, rows_re = [], [], []
            for d in range(AG.D0, AG.D1):
                v = 10.0 + (d % 3)
                rows.append([cell, 0, "AAAUSDT", "BTCUSDT", d, v, v + 12.6, 12.6, 0, 1, 1])
                rows.append([cell, 1, "BBBUSDT", "BTCUSDT", d, -2.0, 10.6, 12.6, 0, 1, 1])
                rows_pl.append([cell, 0, "AAAUSDT", "BTCUSDT@p1", d, -12.6, 0, 12.6, 0, 1, 1])
                rows_re.append([cell, 0, "AAAUSDT", "BTCUSDT", d, -12.6, 0, 12.6, 0, 1])
            self._write(os.path.join(tmp, "w1", "A2_aster_crypto__aster__aster.jobday.csv"), hdr, rows)
            self._write(os.path.join(tmp, "pl", "A2_aster_crypto__aster__aster.jobday.csv"), hdr, rows_pl)
            self._write(os.path.join(tmp, "re", "A2_aster_crypto__aster__aster.jobday.csv"), hdr[:-1], rows_re)
            out = os.path.join(tmp, "agg")
            sm = AG.cmd_cells(dict(run=tmp, out=out, d0=AG.D0, d1=AG.D1))
            self.assertEqual(len(sm), 1)
            with open(os.path.join(out, "cell_summary.csv"), encoding="utf-8") as fh:
                cols = fh.readline().strip().split(",")
            row = dict(zip(cols, sm[0]))
            self.assertEqual(row["days_with_value"], 21)
            self.assertEqual(row["trades"], 42)
            ds = [((10.0 + d % 3) - 2.0) / 2 for d in range(AG.D0, AG.D1)]
            self.assertAlmostEqual(row["mean_bps_day_pair"], sum(ds) / 21, places=9)
            self.assertEqual(row["trades_on_days_without_active_pairs"], 0)
            ctr = AG.cmd_controls(dict(run=tmp, out=out, d0=AG.D0, d1=AG.D1))
            stages = {r[1] for r in ctr}
            self.assertEqual(stages, {"main", "pl", "re"})
            re_row = [r for r in ctr if r[1] == "re"][0]
            self.assertGreater(re_row[8], 0)             # разность «клетка - случайные входы» положительна по построению
            self.assertGreater(re_row[9], 0)             # и нижняя граница > 0
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_slices_hours_hyp_and_session(self):
        tmp = tempfile.mkdtemp()
        try:
            cell = "C1|aster~binance|crypto|w64|a0"
            hdr = ["cell", "job", "y", "x", "day", "net_bps", "gross_bps", "fee_bps", "funding_bps", "trades", "active"]
            self._write(os.path.join(tmp, "w1", "C1_x__binance__aster.jobday.csv"), hdr,
                        [[cell, 0, "LABUSDT", "LABUSDT", 2480, 5.0, 17.6, 12.6, 0, 1, 1], [cell, 1, "AAAUSDT", "AAAUSDT", 2480, -3.0, 9.6, 12.6, 0, 1, 1]])
            thdr = ["cell", "job", "y", "x", "dir", "sig_in", "fill_in", "fill_x", "sig_out", "fill_out", "gross_bps", "fee_bps", "funding_bps", "net_bps", "exit_reason", "beta_in"]
            t0 = 2480 * 1440
            self._write(os.path.join(tmp, "w1", "C1_x__binance__aster.trades.csv"), thdr,
                        [[cell, 0, "LABUSDT", "LABUSDT", 1, t0 + 60, t0 + 61, t0 + 62, t0 + 100, t0 + 101, 17.6, 12.6, 0, 5.0, "rule", 1],
                         [cell, 1, "AAAUSDT", "AAAUSDT", 1, t0 + 7 * 60, t0 + 7 * 60 + 1, t0 + 7 * 60 + 2, t0 + 7 * 60 + 9, t0 + 7 * 60 + 10, 9.6, 12.6, 0, -3.0, "rule", 1]])
            defs = os.path.join(tmp, "defs")
            self._write(os.path.join(defs, "slices_days.csv"), ["slice", "description", "days"], [["d", "x", "2480"]])
            self._write(os.path.join(defs, "slices_hours.csv"), ["slice", "description", "hour_from", "hour_to"], [["H00_05", "x", 0, 5], ["H06_11", "x", 6, 11]])
            self._write(os.path.join(defs, "slices_hyp.csv"), ["slice", "text", "kinds", "ex_pairs", "bases", "availability"],
                        [["H3", "LAB", "C1", "aster~binance", "LAB", ""], ["H5", "PORT3", "", "", "", "нет в данных"]])
            # контракты: LAB и AAA
            ctr = os.path.join(tmp, "contracts.csv")
            self._write(ctr, ["exchange", "symbol", "asset_id", "slug"],
                        [["aster", "LABUSDT", "LAB|крипта", "crypto"], ["binance", "LABUSDT", "LAB|крипта", "crypto"], ["aster", "AAAUSDT", "AAA|крипта", "crypto"], ["binance", "AAAUSDT", "AAA|крипта", "crypto"]])
            rows = AG.cmd_slices(dict(run=tmp, defs=defs, out=os.path.join(tmp, "agg"), d0=AG.D0, d1=AG.D1, contracts=ctr))
            by = {(r[0], r[2]): r for r in rows}
            self.assertEqual(by[("H00_05", cell)][5], 1)       # сделка в 01:01 UTC
            self.assertEqual(by[("H06_11", cell)][5], 1)       # сделка в 07:00 UTC
            self.assertEqual(by[("H3", cell)][3], 1)           # из двух заданий в срез LAB попало одно
            self.assertEqual(by[("H3", cell)][5], 1)
            self.assertIn(("H5", "-"), by)                     # «нет в данных» остаётся строкой
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipIf(shutil.which("bash") is None or sys.platform == "win32" and shutil.which("flock") is None, "нет bash/flock")
class RunScriptTests(unittest.TestCase):
    """Защиты f8_run.sh на фиктивном бинаре и списке из трёх единиц (git и params не нужны: --dry-form пропускает боевые проверки, но не sha бинаря)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fbin = os.path.join(self.tmp, "fakebin")
        with open(self.fbin, "w", newline="\n") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(self.fbin, 0o755)
        self.sha = hashlib.sha256(open(self.fbin, "rb").read()).hexdigest()
        with open(os.path.join(self.tmp, "f8_expected.env"), "w", newline="\n") as fh:
            fh.write("F8_BIN_SHA256=%s\nF8_PARAMS_REL=none.csv\nF8_PARAMS_SHA256=x\nF8_UNITS_SHA256=x\nF8_NEED_GB=0\n" % self.sha)
        self.cwd = os.path.join(self.tmp, "cwd")
        os.makedirs(self.cwd)
        with open(os.path.join(self.tmp, "f8_units.tsv"), "w", newline="\n") as fh:
            fh.write("id\tstage\tcmd\touts\n")
            fh.write("a:one\tw1\techo one > out/w1/one.txt\tout/w1/one.*\n")
            fh.write("a:two\tw1\ttest -f marker_ok && echo two > out/w1/two.txt\tout/w1/two.*\n")
            fh.write("b:three\tw2\techo three > out/w2/three.txt\tout/w2/three.*\n")
        self.script = os.path.join(XD, "f8_run.sh")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_sh(self, *args, env=None):
        e = dict(os.environ, F8_DIR=self.tmp, F8_CWD=self.cwd, F8_BIN=self.fbin, THREADS="1")
        e.update(env or {})
        return subprocess.run(["bash", self.script] + list(args), capture_output=True, text=True, env=e, cwd=self.tmp)

    def test_without_go_nothing_runs(self):
        r = self.run_sh()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("без --go ничего не запускается", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.cwd, "out")))

    def test_bad_binary_sha_refused(self):
        with open(os.path.join(self.tmp, "f8_expected.env"), "a", newline="\n") as fh:
            fh.write("F8_BIN_SHA256=deadbeef\n")
        r = self.run_sh("--go", "--dry-form")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sha256 бинаря", r.stdout)

    def test_stop_on_fail_then_resume_skips_done(self):
        r = self.run_sh("--go", "--dry-form")                  # a:two падает (нет marker_ok): остановка после a:one
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.cwd, ".done", "a_one")))
        self.assertTrue(os.path.exists(os.path.join(self.cwd, ".fail", "a_two")))
        self.assertFalse(os.path.exists(os.path.join(self.cwd, ".done", "b_three")))
        open(os.path.join(self.cwd, "marker_ok"), "w").close()   # «чиним» причину
        r = self.run_sh("--go", "--dry-form")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("a:one: старт", r.stdout)               # готовая единица не перезапускалась
        self.assertTrue(os.path.exists(os.path.join(self.cwd, ".done", "b_three")))
        self.assertTrue(os.path.exists(os.path.join(self.cwd, ".f8_finished")))

    def test_real_mode_refuses_without_git_or_token(self):
        r = self.run_sh("--go")                                  # боевой режим: нет git-дерева у каталога во временной папке и нет токена
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ОТКАЗ", r.stdout)

    def test_only_and_stage_filters(self):
        open(os.path.join(self.cwd, "marker_ok"), "w").close()
        r = self.run_sh("--go", "--dry-form", "--stage", "w2")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.cwd, ".done", "b_three")))
        self.assertFalse(os.path.exists(os.path.join(self.cwd, ".done", "a_one")))


if __name__ == "__main__":
    unittest.main()
