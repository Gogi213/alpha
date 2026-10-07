import os, sys, tempfile, unittest

TMP = tempfile.mkdtemp()
os.environ["REG_DIR"] = TMP
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import guard  # noqa: E402


class GuardTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(guard.ledger_path()):
            os.remove(guard.ledger_path())
        self.d = tempfile.mkdtemp()
        self.cells = os.path.join(self.d, "cells.txt")
        self.out = []

    def write_cells(self, n):
        open(self.cells, "w").write("".join(f"form{i} set{i}\n" for i in range(n)))

    def argv(self):
        return ["bounce-grid", "--cells", self.cells, "--set", "set0:a=1"]

    def chk(self, cls="prod", argv=None, **kw):
        return guard.check(cls, argv or self.argv(), say=self.out.append, **kw)

    def test_same_run_twice_is_skipped(self):
        self.write_cells(10)
        self.assertEqual(self.chk()[0], 0)
        guard.done("prod", 0, self.argv(), result="/data/out/a")
        rc, _ = self.chk()
        self.assertEqual(rc, guard.SKIP)
        self.assertIn("взято из реестра", self.out[0])

    def test_one_new_cell_of_ten_only(self):
        self.write_cells(9)
        guard.done("prod", 0, self.argv(), result="/data/out/a")
        self.write_cells(10)
        rc, info = self.chk()
        self.assertEqual(rc, 0)
        self.assertEqual(open(info["cells"]).read().split("\n")[:-1], ["form9 set9"])

    def test_failed_run_is_not_cached(self):
        self.write_cells(3)
        guard.done("prod", 1, self.argv())
        self.assertEqual(self.chk()[0], 0)

    def test_code_change_is_new_fingerprint(self):
        self.write_cells(3)
        binf = os.path.join(self.d, "lob.bin")
        open(binf, "w").write("v1")
        os.chmod(binf, 0o755)
        a = ["bounce-grid", "--cells", self.cells, binf]
        c1 = guard.context(a)
        open(binf, "w").write("v2 changed")
        self.assertNotEqual(c1["fp"], guard.context(a)["fp"]) if False else None
        # пути к малым файлам входят как скрипты по sha -> отпечаток меняется
        self.assertNotEqual(c1["fp"], guard.context(a)["fp"])

    def test_recompute_needs_why(self):
        self.write_cells(2)
        self.assertEqual(self.chk(recompute=True)[0], guard.REFUSE)
        self.assertEqual(self.chk(recompute=True, why="изменился код")[0], 0)

    def test_measure_same_fingerprint_refused_repeat_allowed(self):
        a = ["bash", "-c", "echo bench"]
        self.assertEqual(self.chk("wave", a)[0], 0)
        guard.done("wave", 0, a, result="/data/tk048/w1")
        self.assertEqual(self.chk("wave", a)[0], guard.SKIP)
        self.assertIn("/data/tk048/w1", self.out[-1])
        self.assertEqual(self.chk("wave", a, repeat=2)[0], 0)
        guard.done("wave", 0, a)
        self.assertEqual(self.chk("wave", a, repeat=2)[0], guard.SKIP)
        self.assertEqual(self.chk("wave", a, recompute=True, why="новый бинарь")[0], 0)


if __name__ == "__main__":
    unittest.main()
