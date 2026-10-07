import os, re, sys, tempfile, unittest

TMP = tempfile.mkdtemp()
os.environ["REG_DIR"] = TMP
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402
import snap  # noqa: E402


def put(path, text):
    with open(path, "w") as f:
        f.write(text)


class GuardTest(unittest.TestCase):
    def setUp(self):
        for f in ("ledger.jsonl", "done-errors.log"):
            if os.path.exists(os.path.join(TMP, f)):
                os.remove(os.path.join(TMP, f))
        self.d = tempfile.mkdtemp()
        self.cells = os.path.join(self.d, "cells.txt")
        self.out = []
        self._re, self._roots = snap.PATHRE, os.environ.get("GUARD_DATA_ROOTS")
        snap.PATHRE = re.compile("(" + re.escape(self.d) + r"[^\s\"']*)")   # как /data/… на сервере
        os.environ["GUARD_DATA_ROOTS"] = self.d

    def tearDown(self):
        snap.PATHRE = self._re
        os.environ.pop("GUARD_DATA_ROOTS") if self._roots is None else os.environ.update(GUARD_DATA_ROOTS=self._roots)

    def write_cells(self, n):
        with open(self.cells, "w") as f:
            f.write("".join(f"form{i} set{i}\n" for i in range(n)))

    def argv(self):
        return ["bounce-grid", "--cells", self.cells, "--out-dir", os.path.join(self.d, "out"), "--set", "set0:a=1"]

    def chk(self, cls="prod", argv=None, **kw):
        return guard.check(cls, argv or self.argv(), say=self.out.append, **kw)

    def run_ok(self, cls="prod", argv=None, rc=0, **kw):
        r, info = self.chk(cls, argv, **kw)
        self.assertEqual(r, 0)
        guard.done(cls, rc, pending=info["pending"])
        return info

    def test_same_run_twice_is_skipped(self):
        self.write_cells(10)
        self.run_ok()
        rc, _ = self.chk()
        self.assertEqual(rc, guard.SKIP)
        self.assertIn("взято из реестра", self.out[0])
        self.assertIn(os.path.join(self.d, "out"), self.out[1])

    def test_one_new_cell_of_ten_only_and_separate_out_dir(self):
        self.write_cells(9)
        self.run_ok()
        self.write_cells(10)
        rc, info = self.chk()
        self.assertEqual(rc, 0)
        a = info["argv"]
        todo = a[a.index("--cells") + 1]
        self.assertEqual(open(todo).read().split("\n")[:-1], ["form9 set9"])
        outd = a[a.index("--out-dir") + 1]
        self.assertNotEqual(outd, os.path.join(self.d, "out"))
        self.assertIn("-part-", outd)
        guard.done("prod", 0, pending=info["pending"])
        self.assertEqual(self.chk()[0], guard.SKIP)
        cells = [r for r in guard.read_ledger() if r["kind"] == "cell" and r["cell"] == "form9 set9"]
        self.assertEqual(cells[0]["result_path"], outd)

    def test_failed_run_is_not_cached(self):
        self.write_cells(3)
        self.run_ok(rc=1)
        self.assertEqual(self.chk()[0], 0)

    def test_binary_replaced_via_path_changes_fingerprint(self):
        self.write_cells(3)
        bd = tempfile.mkdtemp()
        b = os.path.join(bd, "lob.exe" if os.name == "nt" else "lob")
        old_max, old_path = snap.MAXCOPY, os.environ["PATH"]
        snap.MAXCOPY = 4
        os.environ["PATH"] = bd + os.pathsep + old_path
        try:
            put(b, "v1-binary")
            os.chmod(b, 0o755)
            a = ["lob", "bounce-grid", "--cells", self.cells]
            f1 = guard.context(a)["fp"]
            put(b, "v2-binary-changed")
            self.assertNotEqual(f1, guard.context(a)["fp"])
        finally:
            snap.MAXCOPY, os.environ["PATH"] = old_max, old_path

    def test_edit_in_data_root_changes_fingerprint(self):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "BTC"))
        f = os.path.join(root, "BTC", "day.bin")
        put(f, "a")
        a = ["bounce-grid", "--root", root]
        f1 = guard.context(a)["fp"]
        self.assertEqual(f1, guard.context(a)["fp"])
        put(f, "changed!")
        self.assertNotEqual(f1, guard.context(a)["fp"])

    def test_job_writing_into_out_dir_keeps_fingerprint(self):
        self.write_cells(3)
        root = os.path.join(self.d, "roots")
        os.makedirs(root)
        put(os.path.join(root, "x.bin"), "in")
        out = os.path.join(self.d, "out")
        a = ["lob", "bounce-grid", "--cells", self.cells, "--root", root, "--out-dir", out]
        info = self.run_ok(argv=a)
        os.makedirs(os.path.join(out, "set0"))
        put(os.path.join(out, "set0", "rounds.csv"), "rows")
        self.assertEqual(self.chk(argv=a)[0], guard.SKIP)
        put(os.path.join(root, "x.bin"), "input changed")
        self.assertEqual(self.chk(argv=a)[0], 0)

    def test_measure_script_mentioning_output_dirs_is_refused_second_time(self):
        prog = os.path.join(self.d, "progress")
        os.makedirs(prog)
        sc = os.path.join(self.d, "bench.sh")
        put(sc, f"echo run > {prog}/job.json\n")
        a = ["bash", sc]
        self.run_ok("wave", a)
        put(os.path.join(prog, "job.json"), "written by the job")
        self.assertEqual(self.chk("wave", a)[0], guard.SKIP)

    def test_recompute_needs_why(self):
        self.write_cells(2)
        self.assertEqual(self.chk(recompute=True)[0], guard.REFUSE)
        self.assertEqual(self.chk(recompute=True, why="изменился код")[0], 0)
        self.assertTrue(any(r["kind"] == "recompute" for r in guard.read_ledger()))

    def test_measure_same_fingerprint_refused_repeat_allowed(self):
        a = ["bash", "-c", "echo bench"]
        self.run_ok("wave", a)
        self.assertEqual(self.chk("wave", a)[0], guard.SKIP)
        self.run_ok("wave", a, repeat=2)
        self.assertEqual(self.chk("wave", a, repeat=2)[0], guard.SKIP)
        self.assertEqual(self.chk("wave", a, recompute=True, why="новый бинарь")[0], 0)

    def test_wrappers_give_one_fingerprint_on_every_path(self):
        self.write_cells(3)
        core = self.argv()
        base = guard.context(core)["fp"]
        env = os.path.join(self.d, "env-123.sh")
        put(env, "export ALPHA_X=1\n")
        shapes = [
            ["bash", "/data/sched/benchrun-inner.sh", env, "wave"] + core,      # benchrun -> benchrun-sched -> inner
            ["/data/registry/regrun.sh", "prod"] + core,                          # regrun / jobrun
            ["bash", "/data/benchrun.sh", "stand"] + core,
            ["python3", "/data/sched/alsched.py", "wave", "--max-runtime", "2h"] + core,
        ]
        for s in shapes:
            self.assertEqual(guard.core_argv(s), core)
            self.assertEqual(guard.context(s)["fp"], base)
        # другой envfile ($$ в имени) — тот же отпечаток
        env2 = os.path.join(self.d, "env-999.sh")
        put(env2, "export ALPHA_X=1\n")
        self.assertEqual(guard.context(["bash", "/data/sched/benchrun-inner.sh", env2, "wave"] + core)["fp"], base)

    def test_check_then_done_via_wrapper_hits_cache(self):
        self.write_cells(3)
        r, info = guard.check("prod", ["/data/registry/regrun.sh", "prod"] + self.argv(), say=self.out.append)
        guard.done("prod", 0, pending=info["pending"])
        rc, _ = guard.check("prod", self.argv(), say=self.out.append)
        self.assertEqual(rc, guard.SKIP)

    def test_done_failure_leaves_a_trace(self):
        guard.log_failure("тест")
        self.assertIn("тест", open(os.path.join(TMP, "done-errors.log"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
