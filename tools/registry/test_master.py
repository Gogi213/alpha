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
        for hyp, av in (("Г-87", {"n": "2"}), ("Г-87", {"n": "5"}), ("Г-92", {"N": "3", "u": "1/3"}), ("Г-92", {"N": "5", "u": "1"}), ("Г-106", {"x": "1"})):
            cfg = master.p14_cfg(hyp, sv, av)
            self.assertIn(ch.cell_hash(cfg), have, (hyp, av, cfg["form"]))
        cfg = master.p14_cfg("Г-92", sv, {"N": "3", "u": "1/4"})
        self.assertIsNone(master.LINES[ch.cell_hash(cfg)][0])
        with self.assertRaises(SystemExit):
            master.p14_cfg("Г-87", {**sv, "sigma": "sigma120"}, {"n": "2"})

if __name__ == "__main__":
    unittest.main()
