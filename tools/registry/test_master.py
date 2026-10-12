"""Тесты Летописи-мастера (TK-187): load-axes → plan (дедуп по хешу) → sieve → render на временной БД."""
import csv, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("REG_DIR", tempfile.mkdtemp())
import cellhash as ch, db, master, registry  # noqa: E402

PARAMS = "name;unit;resolution;axis_type;cfg_path;note\nn;шт;1;numeric;variant.axes.n;\nu;доля;;log;variant.axes.u;\nfam;;;categorical;variant.family;\nstop;bps;1;numeric;stop;\n"
STACKS = "stack;version;param;value\nB1;v1;fam;g87\nB1;v1;stop;200\n"
AXES = "hyp;protocol;version;stack;param;lo;hi;points_f1;old_points;note\nГ-87;P-14;v1;B1;n;2;8;2|3|4|5|6|7|8;3|4;\n"
RULES = ("protocol;version;key;value\n" + "".join(f"P-14;r1;{k};{v}\n" for k, v in
         (("p_max", 0.2), ("min_trades", 20), ("min_days", 10), ("plateau_min_nb", 5), ("months", 9), ("step_div", 4), ("cap_f2", 12), ("cap_f3", 8), ("cap_cells", 100))))


class MasterTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.regd = os.path.join(self.t, "registry"); os.makedirs(self.regd)
        self.ax = os.path.join(self.t, "axes"); os.makedirs(self.ax)
        self.keep = (db.REGD, db.DB, registry.CANON, registry.DB, master.ROOT)
        db.REGD, db.DB = self.regd, os.path.join(self.t, "data", "registry.sqlite")
        registry.CANON, registry.DB = os.path.join(self.regd, "runs.jsonl"), db.DB
        master.ROOT = self.t
        for fn, body in (("params.csv", PARAMS), ("stacks.csv", STACKS), ("hyp_axes.csv", AXES), ("rules.csv", RULES)):
            open(os.path.join(self.ax, fn), "w", encoding="utf-8").write(body)
        self.keep_have, master.have_hashes = master.have_hashes, lambda c=None: self.have

    def tearDown(self):
        db.REGD, db.DB, registry.CANON, registry.DB, master.ROOT = self.keep
        master.have_hashes = self.keep_have

    def cfg_hash(self, n):
        return ch.cell_hash({"stop": 200, "variant": {"family": "g87", "axes": {"n": n}}})

    def test_g141_gone_wall_names(self):
        """В-227 М3: Г-141 -> gone<W>wall[x|k]<B> (forms.rs GoneWall); вне доменов — причина, не имя."""
        mm = {"gone_w": "GoneWall:pct", "wall_buf_bps": "GoneWall:buffer_bps", "wall_mode": "GoneWall:~"}
        av = {"gone_w": "25", "wall_buf_bps": "4", "wall_mode": "wallk"}
        self.assertEqual(master.r2_exit("Г-141", av, mm, "S"), (None, "gone25wallk4", "S", None))
        self.assertEqual(master.r2_exit("Г-141", {**av, "wall_mode": "wall"}, mm, "S")[1], "gone25wall4")
        self.assertEqual(master.r2_exit("Г-141", {**av, "wall_mode": "wallx"}, mm, "S")[1], "gone25wallx4")
        self.assertIsNone(master.r2_exit("Г-141", {**av, "wall_buf_bps": "-3"}, mm, "S")[1])
        self.assertIsNone(master.r2_exit("Г-141", {**av, "wall_mode": "nope"}, mm, "S")[1])

    def test_g13_sigma_stop_name(self):
        """В-227 М3: Г-13 -> стоп s<a> (StopForm::Sigma); вне домена — причина."""
        sv = {"entry_ttl_s": 1800}
        self.assertEqual(master.sigma_stop({"sigma_stop": "3/4"}), "s0.75")
        self.assertEqual(master.sigma_stop({"sigma_stop": "2"}), "s2")
        self.assertIsNone(master.sigma_stop({"sigma_stop": "5"}))
        self.assertIsNone(master.rust_axes("Г-13", {"sigma_stop": "5"}, sv)[3] and None)
        self.assertIsNotNone(master.rust_axes("Г-13", {"sigma_stop": "5"}, sv)[3])
        self.assertIsNone(master.rust_axes("Г-13", {"sigma_stop": "1"}, sv)[3])

    def test_g147_touch_own_hash_no_line(self):
        """В-227 М3: Г-147 (signal touch) — хеш по варианту, отличается от B1; строки --cells нет (режим шапки)."""
        sv = {"entry_ttl_s": 1800, "stop": "pct2", "take": "1to1", "deadline_s": 3600, "entry": "single", "set": "t-bid-btc4h-q1", "universe": "v171b"}
        c = master.p14_cfg("Г-147", sv, {"signal": "touch"})
        h = ch.cell_hash(c)
        self.assertIsNone(master.LINES[h][0])
        self.assertNotEqual(h, ch.cell_hash({k: v for k, v in c.items() if k != "variant"}))

    def test_m1_new_family_column_outside_r1ext_goes_to_count(self):
        """В-227 М1: колонка вне r1ext (nz_levels15) не «посчитана»; колонка из r1ext с тем же знаком (best_move_1s hi) — посчитана, с другим (ofi lo, Судья 12.10) — к счёту."""
        sv = {"entry_ttl_s": 1800, "stop": "pct2", "take": "1to1", "deadline_s": 3600, "entry": "single", "set": "t-bid-btc4h-q1", "universe": "v171b"}
        new = ch.cell_hash(master.p14_cfg("Г-28", sv, {"col": "nz_levels15", "keep_q": "50"}))
        old = ch.cell_hash(master.p14_cfg("Г-46", sv, {"col": "best_move_1s_cbps", "keep_q": "50"}))
        flip = ch.cell_hash(master.p14_cfg("Г-46", sv, {"col": "ofi_10s_lots", "keep_q": "50"}))
        self.assertNotIn(new, master.R1COL)
        self.assertNotIn(flip, master.R1COL)
        self.assertIn(old, master.R1COL)
        self.assertIn(new, master.R1X)       # TK-202 B11: посчитано tk202-r1x.py
        self.assertIn(flip, master.R1X)
        self.assertNotIn(old, master.R1X)

    def test_m3_ctx_strata_are_distinct_cells_to_count(self):
        """В-227 М3 (Г-78): ось ctx_q даёт 5 разных хешей на keep_q, ни один не «посчитан r1ext»."""
        sv = {"entry_ttl_s": 1800, "stop": "pct2", "take": "1to1", "deadline_s": 3600, "entry": "single", "set": "t-bid-btc4h-q1", "universe": "v171b"}
        hs = {ch.cell_hash(master.p14_cfg("Г-78", sv, {"keep_q": "50", "ctx_q": f"q{i}"}, fixed=("col",))) for i in range(1, 6)}
        self.assertEqual(len(hs), 5)
        self.assertFalse(hs & set(master.R1COL))
        self.assertTrue(hs <= master.R1X)

    def test_bind_hyp_has_no_cells(self):
        """В-227 М4/М8: engine bind:<хозяин> — гипотеза закрыта привязкой, клеток не даёт; хозяин не затронут."""
        self.have = {}
        axes = ("hyp;protocol;version;stack;param;lo;hi;points_f1;old_points;note;engine\n"
                "Г-87;P-14;v1;B1;n;2;8;2|3|4|5|6|7|8;3|4;;rust\n"
                "Г-09;P-14;v1;B1;n;;;;4;;bind:Г-87\n")
        open(os.path.join(self.ax, "hyp_axes.csv"), "w", encoding="utf-8").write(axes)
        master.load_axes(self.ax)
        c = master.connect()
        grids, closed = master.build_grid(c, "P-14", 1)
        c.close()
        self.assertEqual(len(grids["Г-87"]), 7)
        self.assertNotIn("Г-09", grids)
        self.assertEqual(closed["Г-09"], "привязка: ось = Г-87 (клетки там, своих нет)")

    def test_plan_skips_hashes_in_results(self):
        self.have = {self.cfg_hash(3): {"2026-01", "2026-02"}, self.cfg_hash(4): {"2026-01"}}
        self.assertEqual(master.load_axes(self.ax), {"params.csv": 4, "stacks.csv": 2, "hyp_axes.csv": 1, "rules.csv": 9})
        r = master.plan("P-14", 1, out=os.path.join(self.t, "new.jsonl"))
        self.assertEqual((r["grid"], r["have"], r["new"]), (7, 2, 5))
        new = {json.loads(x)["hash"] for x in open(r["out"], encoding="utf-8")}
        self.assertFalse(new & set(self.have))
        self.assertEqual(new, {self.cfg_hash(n) for n in (2, 5, 6, 7, 8)})
        self.assertIn("× 9 мес. = 63 хеш-мес.", r["text"])

    def test_sieve_plateau_and_f2_neighbourhood(self):
        self.have = {}
        master.load_axes(self.ax)
        master.plan("P-14", 1, out=os.path.join(self.t, "new.jsonl"))
        stats = os.path.join(self.t, "stats.csv")
        good = {3, 4, 5}
        with open(stats, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter=";"); w.writerow(["cell_hash", "side", "p", "trades", "days"])
            for n in range(2, 9):
                w.writerow([self.cfg_hash(n), "A", 0.05 if n in good else 0.5, 30, 15])
                w.writerow([self.cfg_hash(n), "B", 0.01, 30, 15])   # сторона Б не читается
        out = master.sieve("P-14", 1, stats)
        plat = {r["cell_hash"] for r in out if r["plateau"]}
        self.assertEqual(plat, {self.cfg_hash(4)})            # 3 и 5 имеют негодного соседа; 4 — оба соседа годны
        self.assertTrue(all(r["side"] == "A" for r in out))
        r2 = master.plan("P-14", 2, out=os.path.join(self.t, "f2.jsonl"))
        ns = {json.loads(x)["variant"]["axes"]["n"] for x in open(r2["out"], encoding="utf-8")}
        self.assertEqual(ns, {3, 4, 5})   # плато {4} + по соседу; шаг 0,25 < разрешимости n (1) — ось не дробится

    def test_f2_closed_without_plateau(self):
        self.have = {}
        master.load_axes(self.ax)
        master.plan("P-14", 1, out=os.path.join(self.t, "new.jsonl"))
        r = master.plan("P-14", 2, out=os.path.join(self.t, "f2.jsonl"))
        self.assertEqual((r["grid"], list(r["closed"])), (0, ["Г-87"]))

    def test_render_equals_db(self):
        self.have = {self.cfg_hash(3): {"2026-01"}}
        master.load_axes(self.ax)
        master.plan("P-14", 1, out=os.path.join(self.t, "new.jsonl"))
        base, rows = master.render("matrix", "2026-10-11", outdir=self.t)
        c = db.sqlite3.connect(db.DB)
        grid = c.execute("select count(distinct cell_hash) from plan_cells where hyp='Г-87'").fetchone()[0]
        c.close()
        row = rows[0]
        self.assertEqual((row[0], row[-3], row[-2], row[-1]), ("Г-87", grid, 1, grid - 1))
        self.assertIn("T:2|3|4|5|6|7|8", row)
        self.assertIn("=200", row)
        csv_rows = list(csv.reader(open(base + ".csv", encoding="utf-8"), delimiter=";"))
        self.assertEqual(csv_rows[1], [str(x) for x in row])
        _, cov = master.render("coverage", "2026-10-11", outdir=self.t)
        self.assertEqual(cov[0][:7], ["P-14", "F1", 1, 7, 1, 6, 1])


    def test_r2_adapter_matches_letopis(self):
        """Г-87 n=2..5, Г-92 pyre3u1, Г-106 nostop — клетки Летописи находятся по хешу; не кодируемые формой значения — без строки --cells."""
        import cellhash_reg as cr
        have = {r[1] for r in cr.build()}
        sv = {"entry": "ladder3x0..0.0409sw2", "stop": "pct2", "take": "tr1x1", "deadline_s": "14400", "entry_ttl_s": "1800", "set": "t-bid-btc4h-q1",
              "universe": "pool v171c (В-210)", "sigma": "sigma240", "queue": "prob:3", "busy": "off"}
        mms = {"Г-87": {"n": "PyrFresh:parts"}, "Г-92": {"N": "PyrReinstall:n", "u": "PyrReinstall:u12*12"}, "Г-106": {"x": "NoStop:x8*8"}}   # колонка hyp_axes.mechanic
        for hyp, av in (("Г-87", {"n": "2"}), ("Г-87", {"n": "5"}), ("Г-92", {"N": "3", "u": "1/3"}), ("Г-92", {"N": "5", "u": "1"}), ("Г-106", {"x": "1"})):
            cfg = master.p14_cfg(hyp, sv, av, mm=mms[hyp])
            self.assertIn(ch.cell_hash(cfg), have, (hyp, av, cfg["form"]))
        # Г-65: offset k -> wall2 / wall2x (Летопись, хеш найден) и новые wall2m<n>/wall20/wall2p<n>
        for off, name in (("-1", "wall2"), ("1", "wall2x"), ("0", "wall20"), ("-3", "wall2m3"), ("2", "wall2p2")):
            cw = master.p14_cfg("Г-65", sv, {"offset": off})
            self.assertTrue(cw["form"].endswith("-" + name), cw["form"])
            self.assertEqual(master.LINES[ch.cell_hash(cw)][0], cw["form"] + " t-bid-btc4h-q1")
        # Г-133: хеш по оси a (числом, как e133-w* Летописи), строки --cells на месяц: chase<round(a·W_m)>
        c3 = master.p14_cfg("Г-133", sv, {"a": "1/2"})
        self.assertEqual(c3["variant"], {"family": "e133", "axes": {"a": 0.5}})
        m3 = master.MONTHLY[ch.cell_hash(c3)]
        self.assertTrue(m3["2026-05"].endswith("-ttl1800-chase9468 t-bid-btc4h-q1"), m3["2026-05"])   # 18935/2 -> 9468 (v6)
        self.assertEqual(len(m3), 9)
        self.assertEqual(master.p14_cfg("Г-133", sv, {"a": "1/8"})["variant"]["axes"], {"a": 0.125})
        # Г-117: тейк 1to1 (стек B3); общие точки — tsl<G>t<Q>, остальные — tslx
        c1 = master.p14_cfg("Г-117", sv, {"gamma": "1", "T": "1/2"})
        self.assertTrue(c1["form"].endswith("-1to1-14400-ttl1800-tsl10t2"), c1["form"])
        self.assertEqual(master.LINES[ch.cell_hash(c1)][0], c1["form"] + " t-bid-btc4h-q1")
        c2 = master.p14_cfg("Г-117", sv, {"gamma": "3/8", "T": "1/16"})
        self.assertTrue(c2["form"].endswith("-1to1-14400-ttl1800-tslx3t1"), c2["form"])
        # Г-10: btc<N>h_max=T_m(N,q) по месяцам — набор g10n<N>q<q>-<месяц> (пороги из btc-thresholds.py)
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tp = os.path.join(td, "t.json")
            json.dump({"2026-01": {"6": {"20": -50.5}}, "2026-02": {"6": {"20": -48.25}}}, open(tp, "w"))
            old_thr, master._BTC_THR = master._BTC_THR, tp
            try:
                c10 = master.p14_cfg("Г-10", sv, {"btc_window_h": "6", "btc_thr_q": "20"})
            finally:
                master._BTC_THR = old_thr
        self.assertEqual(c10["variant"], {"family": "g10", "axes": {"btc_window_h": 6, "btc_thr_q": 20}})
        m10 = master.MONTHLY[ch.cell_hash(c10)]
        self.assertEqual(m10["2026-02"], c10["form"] + " g10n6q20-2026-02")
        self.assertEqual(master.MSETS["g10n6q20-2026-01"], "age=2700,side=bid,btc6h_max=-50.5")
        # В-227 М3: usd_min=T_m(q) по месяцам (Г-89/107/27 — один хеш), Г-32 — ещё страта btc4h внутри базы
        with tempfile.TemporaryDirectory() as td:
            tp = os.path.join(td, "t.json")
            json.dump({"usd": {"2026-01": {"50": 35948}}, "behind": {"2026-01": {"50": 5695}}, "ctx": {"btc4h": {"2026-01": {"q20": -104.65, "q40": -75.6, "q60": -60.8, "q80": -51.2}}}}, open(tp, "w"))
            old_thr, master._TK202_THR = master._TK202_THR, tp
            try:
                cu = master.p14_cfg("Г-89", sv, {"usd_min_q": "50"})
                cu2 = master.p14_cfg("Г-107", sv, {"usd_min_q": "50"})
                c32 = master.p14_cfg("Г-32", sv, {"usd_min_q": "50", "ctx_q": "q3"})
                cb = master.p14_cfg("Г-07", sv, {"behind_min_q": "50"})
            finally:
                master._TK202_THR = old_thr
        self.assertEqual(ch.cell_hash(cu), ch.cell_hash(cu2))
        self.assertEqual(master.MONTHLY[ch.cell_hash(cu)]["2026-01"], cu["form"] + " u50-2026-01")
        self.assertEqual(master.MSETS["u50-2026-01"], "age=2700,side=bid,usd_min=35948,btc4h_max=-44.55")
        self.assertEqual(master.MSETS["u50c3-2026-01"], "age=2700,side=bid,usd_min=35948,btc4h_min=-75.6,btc4h_max=-60.8")
        self.assertNotEqual(ch.cell_hash(c32), ch.cell_hash(cu))
        self.assertNotEqual(ch.cell_hash(cb), ch.cell_hash(cu))
        self.assertEqual(master.MONTHLY[ch.cell_hash(cb)]["2026-01"], cb["form"] + " b50-2026-01")
        self.assertEqual(master.MSETS["b50-2026-01"], "age=2700,side=bid,behind_min=5695,btc4h_max=-44.55")
        # Г-110: early<x> — целые секунды как раньше, 0,5 с — early0.5
        for x, suf in (("0.5", "early0.5"), ("1/2", "early0.5"), ("3", "early3")):
            self.assertTrue(master.p14_cfg("Г-110", sv, {"early_exit_s": x})["form"].endswith("-" + suf), x)
        # Г-112: tape<W>q<Q_m(W,q)> по месяцам из v7; Q_m ≤ 0 — месяц без строки (правило «P > 0»)
        c112 = master.p14_cfg("Г-112", sv, {"W": "30", "q": "50"})
        self.assertEqual(c112["variant"], {"family": "e112", "axes": {"W": 30, "q": 50}})
        m112 = master.MONTHLY[ch.cell_hash(c112)]
        self.assertEqual(len(m112), 9)
        self.assertRegex(m112["2026-03"], r"-ttl1800-tape30q0\.\d+ t-bid-btc4h-q1$")
        c0 = master.p14_cfg("Г-112", sv, {"W": "5", "q": "5"})
        self.assertRegex(master.MONTHLY[ch.cell_hash(c0)]["2026-01"], r"-ttl1800-tape5qp t-bid-btc4h-q1$")   # Q_m ≤ 0 -> «мера > 0»
        self.assertEqual(len(master.MONTHLY[ch.cell_hash(c0)]), 9)
        cfg = master.p14_cfg("Г-92", sv, {"N": "3", "u": "1/4"}, mm=mms["Г-92"])
        self.assertIn("pyre3w3", master.LINES[ch.cell_hash(cfg)][0])   # u = 1/4 -> u12 = 3 (TK-189)
        with self.assertRaises(SystemExit):
            master.p14_cfg("Г-87", {**sv, "sigma": "sigma120"}, {"n": "2"})

if __name__ == "__main__":
    unittest.main()
