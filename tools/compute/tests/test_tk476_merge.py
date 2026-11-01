"""TK-476: слияние стадии (tk476_merge.py --part I/N). Срез по хешу имени: символ не меняет владельца, когда соседи убирают свои файлы
из стадии (индексный срез syms[i::n] сдвигался — два процесса брали один символ); второй процесс на символе получает отказ."""
import gzip, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "tk476_merge.py")
HDR = "t_open_ms,o,h,l,c,v,quote_v,n_trades\n"


def load(root, stage):
    env = dict(os.environ, TK476_ROOT=root, TK476_STAGE=stage)
    return env


class Merge(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.root, self.stage = os.path.join(self.d, "r"), os.path.join(self.d, "s")
        for p in (self.root, self.stage):
            os.makedirs(os.path.join(p, "ex"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def put(self, base, sym, rows):
        with gzip.open(os.path.join(base, "ex", sym + ".csv.gz"), "wt") as g:
            g.write(HDR + "".join("%d,1,1,1,1,1,1,1\n" % t for t in rows))

    def test_owner_stable_when_stage_shrinks(self):
        env = load(self.root, self.stage)
        code = ("import sys; sys.path.insert(0, %r); import tk476_merge as m; "
                "syms = ['S%%03d' %% k for k in range(200)]; short = syms[::3]; "
                "own = lambda L: {s: i for i in range(6) for s in m.pick(L, i, 6)}; a, b = own(syms), own(short); "
                "assert sorted(a) == syms and all(a[s] == b[s] for s in short), 'owner moved'" % os.path.dirname(SCRIPT))
        subprocess.run([sys.executable, "-c", code], env=env, check=True)

    def test_parts_merge_all_once(self):
        for k in range(40):
            self.put(self.stage, "C%02d" % k, range(0, 600, 60))
            if k % 2:
                self.put(self.root, "C%02d" % k, range(300, 900, 60))
        env = load(self.root, self.stage)
        ps = [subprocess.Popen([sys.executable, SCRIPT, "ex", "--apply", "--part", "%d/6" % i], env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for i in range(6)]
        for p in ps:
            self.assertEqual(p.wait(), 0, p.stderr.read())
        self.assertEqual([f for f in os.listdir(os.path.join(self.stage, "ex"))], [])
        self.assertEqual(len(os.listdir(os.path.join(self.root, "ex"))), 40)
        with gzip.open(os.path.join(self.root, "ex", "C01.csv.gz"), "rt") as f:
            self.assertEqual(len(f.read().splitlines()), 1 + 15)   # 0..540 ∪ 300..840 шагом 60

    def test_second_process_on_same_symbol_refused(self):
        self.put(self.stage, "A", range(0, 300, 60))
        self.put(self.root, "A", range(0, 300, 60))
        open(os.path.join(self.stage, "ex", "A.csv.gz.merge.tmp"), "w").close()   # tmp занят другим процессом
        env = load(self.root, self.stage)
        r = subprocess.run([sys.executable, SCRIPT, "ex", "--apply", "--only", "A"], env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FileExistsError", r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.stage, "ex", "A.csv.gz")))


if __name__ == "__main__":
    unittest.main()
