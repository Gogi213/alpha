"""TK-264: страж свежести копии суток на sda в day-cells.sh (блок copy-fresh): совпало → копия, разошлось → sdb + сигнал."""
import os, re, shutil, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "..", "day-cells.sh"), encoding="utf-8").read()
BLOCK = re.search(r"# copy-fresh:begin.*?\n(.*?)# copy-fresh:end", SRC, re.S).group(1)


def find_bash():
    for p in (r"C:\Program Files\Gitinash.exe", "/bin/bash", "/usr/bin/bash"):   # на Windows PATH-bash — WSL, не годится
        if os.path.exists(p):
            return p
    return shutil.which("bash")


BASH = find_bash()


def posix(p):
    return p.replace("\\", "/")


@unittest.skipUnless(BASH, "нужен bash с GNU stat")
class Fresh(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t, True)
        self.root_src, self.root_cp = self.mk("rs"), self.mk("rc")
        self.jrn_src, self.jrn_cp = self.mk("js"), self.mk("jc")
        for b in (self.root_src, self.root_cp):
            for n in ("A.binlog", "A.binlog.events"):
                self.put(os.path.join(b, n), 100, 1000)
        for j in (self.jrn_src, self.jrn_cp):
            os.makedirs(os.path.join(j, "D", "wt"))
            os.utime(os.path.join(j, "D", "wt"), (1000, 1000))
        self.put(os.path.join(self.jrn_src, "D", "done"), 0, 1500)
        self.put(os.path.join(self.jrn_cp, "D", "done"), 0, 2000)   # копия делалась позже исходника

    def mk(self, n):
        p = os.path.join(self.t, n)
        os.makedirs(p)
        return p

    @staticmethod
    def put(p, size, mt):
        with open(p, "wb") as f:
            f.write(b"x" * size)
        os.utime(p, (mt, mt))

    def sh(self, call):
        script = "d=D; O=%s; ALT_ROOT=/x\n%s\n%s\n" % (posix(self.t), BLOCK, call)
        r = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8")
        return r

    def root(self):
        return self.sh("root_fresh '%s' '%s'" % (posix(self.root_src), posix(self.root_cp))).returncode == 0

    def jrn(self):
        return self.sh("jrn_fresh '%s' '%s'" % (posix(self.jrn_src), posix(self.jrn_cp))).returncode == 0

    def test_same_is_fresh(self):
        self.assertTrue(self.root())
        self.assertTrue(self.jrn())

    def test_root_size_changed(self):
        self.put(os.path.join(self.root_src, "A.binlog"), 101, 1000)
        self.assertFalse(self.root())

    def test_root_mtime_changed(self):
        os.utime(os.path.join(self.root_src, "A.binlog.events"), (1001, 1001))
        self.assertFalse(self.root())

    def test_root_file_added_or_removed(self):
        self.put(os.path.join(self.root_src, "B.binlog"), 5, 1000)
        self.assertFalse(self.root())
        os.remove(os.path.join(self.root_src, "B.binlog"))
        os.remove(os.path.join(self.root_cp, "A.binlog"))
        self.assertFalse(self.root())

    def test_root_source_missing_keeps_copy(self):
        shutil.rmtree(self.root_src)
        self.assertTrue(self.root())

    def test_jrn_source_done_newer(self):
        os.utime(os.path.join(self.jrn_src, "D", "done"), (2500, 2500))
        self.assertFalse(self.jrn())

    def test_jrn_wt_recreated(self):
        os.utime(os.path.join(self.jrn_src, "D", "wt"), (1700, 1700))
        self.assertFalse(self.jrn())

    def test_stale_signal(self):
        alerts = os.path.join(self.t, "alerts.log")
        r = self.sh("ALERTS='%s' stale 'причина'" % posix(alerts))
        self.assertEqual(r.returncode, 0)
        for f in (os.path.join(self.t, "stale-copy.txt"), alerts):
            txt = open(f, encoding="utf-8").read()
            self.assertIn("КОПИЯ D УСТАРЕЛА (причина)", txt)
        self.assertIn("УСТАРЕЛА", r.stderr)

    def test_stale_signal_unwritable_alerts_is_silent(self):
        r = self.sh("ALERTS='%s/нет/alerts.log' stale 'п'" % posix(self.t))
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("No such file", r.stderr)


if __name__ == "__main__":
    unittest.main()
