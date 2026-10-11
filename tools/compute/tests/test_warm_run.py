"""КТ-2 (TK-148): warm-run — проверка клеток и сборки, шаг через guard (готовый пропускается), накладная в JSON."""
import importlib.machinery, importlib.util, json, os, sys, tempfile, unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "..", "registry"))
sys.path.insert(0, os.path.join(HERE, ".."))
_ld = importlib.machinery.SourceFileLoader("warm_run", os.path.join(HERE, "..", "warm-run"))
W = importlib.util.module_from_spec(importlib.util.spec_from_loader("warm_run", _ld))
_ld.exec_module(W)

MD5 = "a" * 32


class WarmRun(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        os.environ["REG_DIR"] = self.d
        W.guard.snap.REG = self.d
        self.man = os.path.join(self.d, "MANIFEST.tsv")
        os.environ["SCHED_MANIFEST_FILE"] = self.man
        self.set_status("active")
        self.cells = os.path.join(self.d, "cells.txt")
        open(self.cells, "w").write("stop2 setA\nstop3 setA\n")
        self.indir = os.path.join(self.d, "in")
        os.mkdir(self.indir)
        open(os.path.join(self.indir, "a"), "w").close()
        self.out = os.path.join(self.d, "out.txt")
        self.timing = os.path.join(self.d, "t.jsonl")

    def set_status(self, st):
        open(self.man, "w").write("\t".join([MD5, "c0ffee", "pgo", "", st, "t", "/bin/x", ""]) + "\n")

    def run_wr(self, cmd=None, **kw):
        cmd = cmd or [sys.executable, "-c", f"open({self.out!r},'w').write('x')", kw.get("cells", self.cells)]
        a = ["--cells", kw.get("cells", self.cells), "--build-id", kw.get("build", MD5[:8]), "--step", "s1",
             "--out", self.out, "--in", self.indir, "--timing-out", self.timing] + kw.get("extra", []) + ["--"] + cmd
        return W.main(a)

    def test_cells_path_must_be_in_command(self):
        with self.assertRaises(SystemExit):
            self.run_wr(cmd=[sys.executable, "-c", "pass"])
        self.assertFalse(os.path.exists(self.timing))

    def test_runs_then_skips_ready_step(self):
        self.assertEqual(self.run_wr(), 0)
        self.assertTrue(os.path.exists(self.out))
        self.assertEqual(self.run_wr(), 0)
        r = [json.loads(x) for x in open(self.timing)]
        self.assertFalse(r[0]["skipped"])
        self.assertTrue(r[1]["skipped"])
        self.assertEqual(r[0]["cells"], 2)
        self.assertGreaterEqual(r[0]["child_s"], 0)

    def test_unknown_or_retired_build_refused(self):
        with self.assertRaises(SystemExit):
            self.run_wr(build="deadbeef")
        self.set_status("retired")
        with self.assertRaises(SystemExit):
            self.run_wr()

    def test_bad_cells_refused(self):
        open(self.cells, "w").write("only_one_token\n")
        with self.assertRaises(SystemExit):
            self.run_wr()
        open(self.cells, "w").write("")
        with self.assertRaises(SystemExit):
            self.run_wr()

    def test_min_cells_warns_only(self):
        self.assertEqual(self.run_wr(extra=["--min-cells", "10"]), 0)

    def test_run_json_written_when_run_dir_set(self):
        rd = os.path.join(self.d, "runs", "J1")
        os.environ["ALSCHED_RUN_DIR"] = rd
        try:
            self.assertEqual(self.run_wr(), 0)
        finally:
            del os.environ["ALSCHED_RUN_DIR"]
        m = json.load(open(os.path.join(rd, "run.json"), encoding="utf-8"))
        self.assertEqual(m["step"], "s1")
        self.assertEqual(m["out"], self.out)
        self.assertEqual(len(m["cells_sha256"]), 64)
        self.assertEqual(m["build"], MD5)
        self.assertIn("fp", m)

    def test_failing_command_rc_propagates(self):
        self.assertEqual(self.run_wr(cmd=[sys.executable, "-c", "raise SystemExit(7)", self.cells]), 7)


if __name__ == "__main__":
    unittest.main()
