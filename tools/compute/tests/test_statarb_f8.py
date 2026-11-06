"""TK-493 S3 + w5 F: тесты контролей и запуска F8 (f8_shift, f8_lists, f8_randentry, f8_agg, f8_p1, f8_act, f8_handexample, f8_run.sh). Торговое окно не читается:
синтетика и закоммиченные списки S1/S2."""
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
    import f8_p1 as P1
    import f8_act as ACT
except ImportError:      # numpy нет - тесты симуляции и панелей пропускаются
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

    def test_gen_has_all_stages_and_flags(self):
        """Состав единиц F8 (w5 F): стадии, PGO-бинарь, 8 потоков, флаги D5(ii)/D10/D11, пути только внутри рабочего каталога и сухого корня."""
        import f8_lists as L
        if not os.path.exists(os.path.join(L.SELDOC, "waves_manifest.csv")) or not os.path.exists(L.CONTRACTS):
            self.skipTest("нет манифеста волн или contracts.csv")
        tmp = tempfile.mkdtemp()
        try:
            for mode in ("form", "trade"):
                units = os.path.join(tmp, mode + ".tsv")
                us = L.gen(mode, os.path.join(tmp, "lists_" + mode), units)
                stages = {u[1] for u in us}
                self.assertEqual(stages, {"w1", "w2", "w3", "w4", "mkplac", "pl", "rp", "sm", "n17", "mk1", "p1", "act", "flags", "re"})
                groups = L.load_groups()
                self.assertEqual(sum(1 for u in us if u[1] == "p1"), len(groups))               # +1 мин: тот же состав клеток
                self.assertEqual(sum(1 for u in us if u[1] == "pl"), len(groups))
                self.assertEqual(sum(1 for u in us if u[1] == "w4"), sum(1 for g in groups if g["kind"] == "A1"))
                for uid, st, cmd, outs in us:
                    self.assertNotIn("4ef45cc8", cmd, uid)
                    self.assertNotIn("sel/f8_dry", cmd, uid)
                    self.assertNotIn("--threads 4", cmd, uid)
                    if "statarb run" in cmd:
                        self.assertIn("statarb-xdl9-a24f4c96-pgo", cmd, uid)
                        self.assertIn("--threads ${THREADS:-8}", cmd, uid)
                        if st in ("w1", "w2", "w3", "pl", "rp", "sm", "n17", "p1", "w4"):
                            self.assertIn("--mtm-open", cmd, uid)
                        if "--kind funding-pp" in cmd:
                            self.assertIn("--no-entry-before-from", cmd, uid)
                        if st in ("w1", "w2", "w3"):
                            self.assertIn("--job-ext-out", cmd, uid)
                            self.assertIn("--trades-ext", cmd, uid)
                    if st == "p1":
                        self.assertIn("/trade1/" if mode == "trade" else "/form1/", cmd, uid)
                self.assertTrue(os.path.exists(os.path.join(tmp, "lists_" + mode, "p1_needs.tsv")))
                self.assertTrue(os.path.exists(os.path.join(tmp, "lists_" + mode, "act", "main.tsv")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


@unittest.skipIf(np is None, "нет numpy")
class P1ActTests(unittest.TestCase):
    def test_p1_selftest(self):
        P1.selftest()

    def test_act_selftest(self):
        ACT.selftest()


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
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)

    def test_selftest(self):
        AG.selftest()

    def test_hand_example_three_pairs_to_status(self):
        """Ручной пример с известным ответом: 3 пары -> f8_agg.py cells (CLI) -> cell_status; каждое число сверено независимой формулой."""
        import f8_handexample as HX
        self.assertEqual(HX.main(), 0)

    def test_cells_refuse_without_contracts(self):
        tmp = tempfile.mkdtemp()
        try:
            with self.assertRaises(SystemExit):
                AG.cmd_cells(dict(run=tmp, out=os.path.join(tmp, "o"), d0=AG.D0, d1=AG.D1))              # --contracts не задан (аудит M3-02)
            ctr = os.path.join(tmp, "empty.csv")
            self._write(ctr, ["exchange", "symbol", "asset_id", "slug"], [])
            with self.assertRaises(SystemExit):
                AG.cmd_cells(dict(run=tmp, out=os.path.join(tmp, "o"), d0=AG.D0, d1=AG.D1, contracts=ctr))   # файл пуст
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_d2_denominator_from_panels_and_orphans(self):
        """D2: знаменатель - активность по панелям (act/main), а не z ядра (active = 0 в jobday); сделки в (задание, сутки) без активности считаются сиротами по (задание, сутки)."""
        tmp = tempfile.mkdtemp()
        try:
            cell = "D2|binance~kraken|crypto|a0"
            jd = ["cell", "job", "y", "x", "day", "net_bps", "gross_bps", "fee_bps", "funding_bps", "trades", "active"]
            rows = []
            for d in range(AG.D0, AG.D1):
                rows.append([cell, 0, "AAAUSDT", "AAAUSDT", d, 10.0, 22.6, 12.6, 0, 1, 0])             # z ядра не определён: active = 0
                rows.append([cell, 1, "BBBUSDT", "BBBUSDT", d, 6.0, 18.6, 12.6, 0, 1, 0])
            self._write(os.path.join(tmp, "w1", "D2_binance-kraken_crypto__binance__kraken__shard0.jobday.csv"), jd, rows)
            act = []
            for d in range(AG.D0, AG.D1):
                act.append([cell, "AAAUSDT", "AAAUSDT", d, 1])
                act.append([cell, "BBBUSDT", "BBBUSDT", d, 1 if d != AG.D0 else 0])                    # на первых сутках пара 2 неактивна, а сделка есть
            self._write(os.path.join(tmp, "act", "main", "D2_binance-kraken_crypto__binance__kraken.act.csv"), ["cell", "y", "x", "day", "active"], act)
            run = AG.Run(tmp, AG.MAIN_STAGES, AG.D0, AG.D1, act=AG.load_act(tmp, "main"))
            keys = run.keys_of(cell)
            self.assertEqual(len(keys), 2)
            sm = run.sums(keys, range(AG.D0, AG.D1))
            self.assertEqual(sm["active"], 2 * 21 - 1)
            n, net = run.orphans(keys, range(AG.D0, AG.D1))
            self.assertEqual((n, net), (1, 6.0))
            s = run.series(cell, keys, list(range(AG.D0, AG.D1)))
            self.assertEqual(len(s["closed"]), 21)
            self.assertAlmostEqual(s["closed"][1], 8.0)                                                  # (10 + 6)/2 на обычные сутки
            self.assertAlmostEqual(s["closed"][0], (10.0 + 6.0) / 1)                                     # в числителе обе сделки, в знаменателе одна пара: сирота виден в orphans
            # пара D2 без записи в act получает active = 0 (act_missing), а не z ядра
            run2 = AG.Run(tmp, AG.MAIN_STAGES, AG.D0, AG.D1, act={})
            self.assertEqual(len(run2.act_missing), 2)
            self.assertEqual(run2.sums(run2.keys_of(cell), range(AG.D0, AG.D1))["active"], 0)
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
                        [["H3", "LAB", "C1", "aster~binance", "LAB", ""], ["H5", "PORT3", "", "", "", "нет в данных"], ["H1", "ZZZ", "C1", "aster~binance", "ZZZ", ""]])
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
            self.assertIn("нет данных", by[("H5", "-")][-1])
            self.assertIn(("H1", "-"), by)                     # D8: пары гипотезы вне торгуемых клеток - строка «нет данных», а не пропуск и не чужие параметры
            self.assertIn("нет данных", by[("H1", "-")][-1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


GIT = shutil.which("git")
FLOCK = shutil.which("flock")


@unittest.skipIf(shutil.which("bash") is None or FLOCK is None, "нет bash/flock")
class RunScriptTests(unittest.TestCase):
    """Защиты f8_run.sh на фиктивном бинаре и списке из трёх единиц (git и params не нужны: --dry-form пропускает боевые проверки, но не sha бинаря)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fbin = os.path.join(self.tmp, "fakebin")
        with open(self.fbin, "w", newline="\n") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(self.fbin, 0o755)
        with open(self.fbin, "rb") as fh:
            self.sha = hashlib.sha256(fh.read()).hexdigest()
        with open(os.path.join(self.tmp, "f8_expected.env"), "w", newline="\n") as fh:
            fh.write("F8_BIN_SHA256=%s\nF8_PARAMS_REL=none.csv\nF8_PARAMS_SHA256=x\nF8_PROTO_REL=none.md\nF8_PROTO_SHA256=x\nF8_UNITS_SHA256=x\nF8_NEED_GB=0\n" % self.sha)
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

    def test_threads_above_limit_refused(self):
        r = self.run_sh("--go", "--dry-form", env={"THREADS": "9"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("THREADS=9", r.stdout)

    def test_eight_threads_allowed(self):
        open(os.path.join(self.cwd, "marker_ok"), "w").close()
        r = self.run_sh("--go", "--dry-form", "--stage", "w2", env={"THREADS": "8"})
        self.assertEqual(r.returncode, 0, r.stdout)


@unittest.skipIf(shutil.which("bash") is None or FLOCK is None or GIT is None, "нет bash/flock/git")
class RunScriptRealModeTests(unittest.TestCase):
    """Боевые защиты f8_run.sh в настоящем git-дереве (временный репозиторий: params, протокол и список закоммичены; скрипт лежит внутри него).
    Каждая защита проверяется поломкой: правка params / протокола / списка после коммита, непустой out, метка .f8_started от другого params, чужой замок,
    подмена бинаря, потоков больше 8."""

    def sh(self, *a, cwd=None, env=None, check=True):
        e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        e.update(env or {})
        r = subprocess.run(list(a), capture_output=True, text=True, cwd=cwd or self.repo, env=e)
        if check and r.returncode != 0:
            raise AssertionError("%s: %s%s" % (a, r.stdout, r.stderr))
        return r

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(self.repo, "docs", "research"))
        os.makedirs(os.path.join(self.repo, "tools"))
        self.fbin = os.path.join(self.tmp, "fakebin")
        with open(self.fbin, "w", newline="\n") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(self.fbin, 0o755)
        with open(self.fbin, "rb") as fh:
            self.bsha = hashlib.sha256(fh.read()).hexdigest()
        self.params = os.path.join(self.repo, "docs", "research", "params.csv")
        self.proto = os.path.join(self.repo, "docs", "research", "proto.md")
        self.units = os.path.join(self.repo, "tools", "f8_units.tsv")
        for p, t in ((self.params, "key,value\nk,1\n"), (self.proto, "# protocol\n"),
                     (self.units, "id\tstage\tcmd\touts\na:one\tw1\techo one > out/w1/one.txt\tout/w1/one.*\n")):
            with open(p, "w", newline="\n") as fh:
                fh.write(t)
        shutil.copy(os.path.join(XD, "f8_run.sh"), os.path.join(self.repo, "tools", "f8_run.sh"))
        self.sh("git", "init", "-q")
        self.sh("git", "add", "-A")
        self.sh("git", "commit", "-q", "-m", "init")
        sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
        self.env_path = os.path.join(self.repo, "tools", "f8_expected.env")
        with open(self.env_path, "w", newline="\n") as fh:
            fh.write("F8_BIN_SHA256=%s\nF8_PARAMS_REL=docs/research/params.csv\nF8_PARAMS_SHA256=%s\nF8_PROTO_REL=docs/research/proto.md\nF8_PROTO_SHA256=%s\n"
                     "F8_UNITS_SHA256=%s\nF8_NEED_GB=0\n" % (self.bsha, sha(self.params), sha(self.proto), sha(self.units)))
        self.cwd = os.path.join(self.tmp, "cwd")
        os.makedirs(self.cwd)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def go(self, *args, env=None):
        e = dict(os.environ, HOME=os.path.join(self.tmp, "home"), F8_CWD=self.cwd, F8_BIN=self.fbin, F8_UNITS=self.units, THREADS="2")
        e.update(env or {})
        return subprocess.run(["bash", os.path.join(self.repo, "tools", "f8_run.sh"), "--go"] + list(args), capture_output=True, text=True, env=e, cwd=self.tmp)

    def test_clean_run_passes_and_writes_started(self):
        r = self.go()
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.cwd, ".f8_started")))
        self.assertIn("started_proto_sha256=", open(os.path.join(self.cwd, ".f8_started"), encoding="utf-8").read())

    def test_params_changed_after_commit_refused(self):
        with open(self.params, "a", newline="\n") as fh:
            fh.write("k2,2\n")
        r = self.go()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("изменён относительно HEAD", r.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.cwd, ".f8_started")))

    def test_proto_changed_after_commit_refused(self):
        with open(self.proto, "a", newline="\n") as fh:
            fh.write("x\n")
        r = self.go()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("протокол", r.stdout)

    def test_units_changed_refused(self):
        with open(self.units, "a", newline="\n") as fh:
            fh.write("b:two\tw1\techo\tout/w1/x.*\n")
        r = self.go()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("список единиц", r.stdout)

    def test_expected_sha_mismatch_refused(self):
        text = open(self.env_path, encoding="utf-8").read().replace("F8_PROTO_SHA256=", "F8_PROTO_SHA256=00")
        with open(self.env_path, "w", newline="\n") as fh:
            fh.write(text)
        r = self.go()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sha256", r.stdout)

    def test_nonempty_out_before_first_run_refused(self):
        os.makedirs(os.path.join(self.cwd, "out"))
        open(os.path.join(self.cwd, "out", "x"), "w").close()
        r = self.go()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("не пуст", r.stdout)

    def test_existing_d2_dir_before_first_run_refused(self):
        os.makedirs(os.path.join(self.cwd, "d2_p1"))
        r = self.go()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("d2_p1", r.stdout)

    def test_resume_refused_when_started_with_other_params(self):
        self.assertEqual(self.go().returncode, 0)
        text = open(os.path.join(self.cwd, ".f8_started"), encoding="utf-8").read().replace("started_params_sha256=", "started_params_sha256=ff")
        with open(os.path.join(self.cwd, ".f8_started"), "w", newline="\n") as fh:
            fh.write(text)
        shutil.rmtree(os.path.join(self.cwd, ".done"))
        r = self.go()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("возобновление запрещено", r.stdout)

    def test_second_concurrent_run_refused_by_lock(self):
        import time
        lock = os.path.join(self.cwd, ".f8_lock")
        os.makedirs(self.cwd, exist_ok=True)
        holder = subprocess.Popen(["bash", "-c", "exec 9>%s; flock -n 9 && sleep 20" % lock])
        try:
            time.sleep(0.6)
            r = self.go()
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("уже идёт другой запуск", r.stdout)
        finally:
            holder.kill()
            holder.wait()

    def test_wrong_binary_refused(self):
        with open(self.fbin, "a", newline="\n") as fh:
            fh.write("# tampered\n")
        r = self.go()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("sha256 бинаря", r.stdout)

    def test_nine_threads_refused(self):
        r = self.go(env={"THREADS": "9"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("THREADS=9", r.stdout)

    def test_token_mode_on_copy_without_git(self):
        """Deck: копия без git + токен f8_git_ok + копии params и протокола с теми же sha256."""
        deck = os.path.join(self.tmp, "deckdir")
        os.makedirs(deck)
        shutil.copy(os.path.join(XD, "f8_run.sh"), deck)
        shutil.copy(self.env_path, deck)
        shutil.copy(self.units, os.path.join(deck, "f8_units.tsv"))
        r = subprocess.run(["bash", os.path.join(self.repo, "tools", "f8_run.sh"), "--git-token"], capture_output=True, text=True, env=dict(os.environ, F8_UNITS=self.units))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        shutil.copy(os.path.join(self.repo, "tools", "f8_git_ok"), deck)
        home = os.path.join(self.tmp, "home", "xdl9", "sel")
        os.makedirs(home)
        shutil.copy(self.params, home)
        shutil.copy(self.proto, home)
        e = dict(os.environ, HOME=os.path.join(self.tmp, "home"), F8_CWD=self.cwd, F8_BIN=self.fbin, F8_UNITS=os.path.join(deck, "f8_units.tsv"), THREADS="2")
        ok = subprocess.run(["bash", os.path.join(deck, "f8_run.sh"), "--go"], capture_output=True, text=True, env=e, cwd=self.tmp)
        self.assertEqual(ok.returncode, 0, ok.stdout)
        # подмена копии протокола на Deck - отказ
        with open(os.path.join(home, "proto.md"), "a", newline="\n") as fh:
            fh.write("tampered\n")
        shutil.rmtree(self.cwd)
        os.makedirs(self.cwd)
        bad = subprocess.run(["bash", os.path.join(deck, "f8_run.sh"), "--go"], capture_output=True, text=True, env=e, cwd=self.tmp)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("протокола", bad.stdout)


if __name__ == "__main__":
    unittest.main()
