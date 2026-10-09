"""Тесты Летописи (TK-134, С-44): add / build / load_cells / bind-verdicts / snap begin+end на временной БД."""
import json, os, sqlite3, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("REG_DIR", tempfile.mkdtemp())   # snap.REG читается при импорте; общий с test_guard
import db, registry, bind_verdicts, snap  # noqa: E402


def wjl(path, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.regd = os.path.join(self.t, "registry")
        os.makedirs(self.regd)
        self.keep = (db.REGD, db.DB, registry.CANON, registry.DB, bind_verdicts.ROOT)
        db.REGD, db.DB = self.regd, os.path.join(self.t, "data", "registry.sqlite")
        registry.CANON, registry.DB = os.path.join(self.regd, "runs.jsonl"), db.DB
        bind_verdicts.ROOT = self.t

    def tearDown(self):
        db.REGD, db.DB, registry.CANON, registry.DB, bind_verdicts.ROOT = self.keep


class AddBuildTest(Base):
    def test_add_ids_and_status(self):
        a = registry.append({"ts": "2026-10-01T10:00:00+04:00", "what": "первый", "status": "боевой"})
        b = registry.append({"ts": "2026-10-01T11:00:00+04:00", "what": "второй"})
        self.assertEqual((a, b), ("R-20261001-001", "R-20261001-002"))
        self.assertEqual([r["what"] for r in registry.load()], ["первый", "второй"])

    def test_add_rejects_dup_and_bad_status(self):
        registry.append({"id": "R-1", "ts": "2026-10-01T10:00:00+04:00", "what": "x"})
        with self.assertRaises(SystemExit):
            registry.append({"id": "R-1", "ts": "2026-10-01T10:00:00+04:00", "what": "y"})
        with self.assertRaises(SystemExit):
            registry.append({"ts": "2026-10-01T10:00:00+04:00", "what": "z", "status": "нет такого"})

    def test_build_into_sqlite(self):
        registry.append({"ts": "2026-10-01T10:00:00+04:00", "what": "w", "ticket": "TK-1", "binary_md5": "aa,bb"})
        _, n = registry.build()
        self.assertEqual(n, 1)
        c = sqlite3.connect(db.DB)
        self.assertEqual(c.execute("select count(*) from runs").fetchone()[0], 1)
        self.assertEqual(c.execute("select count(*) from run_binaries").fetchone()[0], 2)

    def test_norm_ts_to_plus4(self):
        self.assertEqual(db.norm_ts("2026-10-01T06:00:00+00:00"), "2026-10-01T10:00:00+04:00")


class CellsTest(Base):
    def test_load_cells_no_dups(self):
        cells = os.path.join(self.t, "cells.txt")
        open(cells, "w").write("pct1 setA\npct2 setA\n")
        self.assertEqual(db.load_cells(cells, "R-1", "Г-1", "bounce-v1"), (2, 2))
        self.assertEqual(db.load_cells(cells, "R-1", "Г-1", "bounce-v1"), (0, 0))
        self.assertEqual(db.load_cells(cells, "R-2", "Г-1", "bounce-v1"), (0, 2))

    def test_parse_cmd(self):
        g, sets = db.parse_cmd("lob bounce-grid --h3-mode floor --set A:stop=2,take=3")
        self.assertEqual(g["h3-mode"], "floor")
        self.assertEqual(sets["A"], {"stop": "2", "take": "3"})


class VerdictTest(Base):
    def test_bind_review_to_ticket_runs(self):
        registry.append({"id": "R-1", "ts": "2026-09-30T10:00:00+04:00", "what": "w", "ticket": "TK-123"})
        rev = os.path.join(self.t, "docs", "research", "reviews")
        os.makedirs(rev)
        open(os.path.join(rev, "tk123-review.md"), "w", encoding="utf-8").write("Принято: цифры сходятся\n")
        wjl(os.path.join(self.regd, "verdicts.jsonl"), [
            {"id": "V-tk123-review", "verdict": "см. текст", "review_path": "docs/research/reviews/tk123-review.md",
             "ts": "2026-10-01", "note": ""}])
        bound, rest, judged = bind_verdicts.run(registry.CANON)
        self.assertEqual((bound, rest, judged), (1, 0, 1))
        v = db.jl("verdicts.jsonl")[0]
        self.assertEqual((v["id"], v["run_id"], v["verdict"]), ("V-tk123-review@R-1", "R-1", "принято"))
        self.assertTrue(registry.load()[0]["judge"].startswith("принято"))
        self.assertEqual(bind_verdicts.run(registry.CANON)[0], 1)   # повтор не плодит строки
        self.assertEqual(len(db.jl("verdicts.jsonl")), 1)

    def test_methodology_review_has_no_run(self):
        registry.append({"id": "R-1", "ts": "2026-09-30T10:00:00+04:00", "what": "w", "ticket": "TK-123"})
        wjl(os.path.join(self.regd, "verdicts.jsonl"), [
            {"id": "V-P-12-protocol", "verdict": "принято", "review_path": "x.md", "ts": "2026-10-01", "note": ""}])
        self.assertEqual(bind_verdicts.run(registry.CANON)[:2], (0, 1))
        self.assertTrue(db.jl("verdicts.jsonl")[0]["note"].startswith("[методика/протокол"))


class SnapTest(unittest.TestCase):
    def test_begin_end_manifest_and_auto(self):
        script = os.path.join(tempfile.mkdtemp(), "job.sh")
        open(script, "w").write("echo hi\n")
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            snap.begin("wave", ["bash", script])
        path = buf.getvalue().strip()
        man = json.load(open(path))
        self.assertIn(script, man["files"])
        os.environ["GUARD_PENDING"] = "1"      # хвост alsched: журнал отпечатков не трогаем
        try:
            snap.end(path, 0)
        finally:
            os.environ.pop("GUARD_PENDING")
        man = json.load(open(path))
        self.assertEqual(man["rc"], 0)
        self.assertNotIn("t0", man)
        auto = [json.loads(x) for x in open(os.path.join(snap.REG, "auto.jsonl"), encoding="utf-8")]
        self.assertEqual(auto[-1]["manifest"], os.path.basename(path))


if __name__ == "__main__":
    unittest.main()
