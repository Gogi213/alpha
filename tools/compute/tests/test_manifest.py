"""КТ-1 (TK-145): манифест сборок, обёртки, --adhoc, чистый master, check-sprawl."""
import importlib.util, os, subprocess, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(HERE, "..", "..")


def load(name, path):
    s = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


M = load("manifest", os.path.join(HERE, "..", "manifest.py"))
S = load("sprawl", os.path.join(TOOLS, "check-sprawl.py"))


class Check(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.bin = os.path.join(self.d, "b1")
        with open(self.bin, "wb") as f:
            f.write(b"x" * (M.MIN_BIN + 5))
        os.chmod(self.bin, 0o755)
        self.mf = os.path.join(self.d, "MANIFEST.tsv")
        os.environ["SCHED_MANIFEST_FILE"] = self.mf
        os.environ["SCHED_MANIFEST"] = "1"
        self.alerts = []

    def row(self, status="active"):
        md5 = M.md5_of(self.bin)
        M.write(self.mf, {md5: dict(md5=md5, commit="c", profile="release", features="", status=status, built="t",
                                    path=self.bin, used_by="")})

    def test_bare_binary_refused(self):
        self.assertEqual(M.check_submit([self.bin, "x"], "", self.alerts.append), 3)

    def test_script_outside_whitelist_refused(self):
        self.row()
        self.assertEqual(M.check_submit(["python3", "/data/tk999/foo.py", self.bin], "", self.alerts.append), 3)

    def test_wrapper_with_active_binary_ok(self):
        self.row()
        self.assertEqual(M.check_submit(["warm-run", "--build-id", "c", self.bin], "", self.alerts.append), 0)

    def test_retired_binary_refused(self):
        self.row("retired")
        self.assertEqual(M.check_submit(["benchrun.sh", self.bin], "", self.alerts.append), 3)

    def test_adhoc_logs_alert(self):
        self.assertEqual(M.check_submit([self.bin], "нужно срочно", self.alerts.append), 0)
        self.assertIn("нужно срочно", self.alerts[0])

    def test_warn_mode_passes_and_alerts(self):
        os.environ["SCHED_MANIFEST"] = "warn"
        self.assertEqual(M.check_submit([self.bin], "", self.alerts.append), 0)
        self.assertTrue(any("предупреждение" in a for a in self.alerts))

    def test_off_mode(self):
        os.environ["SCHED_MANIFEST"] = "0"
        self.assertEqual(M.check_submit([self.bin], "", self.alerts.append), 0)
        self.assertEqual(self.alerts, [])

    def test_bootstrap_adds_once_and_keeps_files(self):
        shutil_dir = os.path.join(self.d, "bins")
        os.makedirs(shutil_dir)
        for n in ("alpha-x", "other"):
            with open(os.path.join(shutil_dir, n), "wb") as f:
                f.write(n.encode() * (M.MIN_BIN // 2))
            os.chmod(os.path.join(shutil_dir, n), 0o755)
        self.assertEqual(M.bootstrap(self.mf, [shutil_dir]), 1)
        self.assertEqual(M.bootstrap(self.mf, [shutil_dir]), 0)
        self.assertEqual(sorted(os.listdir(shutil_dir)), ["alpha-x", "other"])

    def test_used_by_written_once(self):
        self.row()
        md5 = M.md5_of(self.bin)
        M.used_by(self.mf, md5, "TK-1")
        M.used_by(self.mf, md5, "TK-1")
        self.assertEqual(M.load(self.mf)[md5]["used_by"], "TK-1")


def git(d, *a):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a], cwd=d, check=True, capture_output=True)


def bash():
    gb = r"C:/Program Files/Git/bin/bash.exe"        # на Windows «bash» в PATH — WSL
    return gb if os.path.exists(gb) else "bash"


def build(d):
    return subprocess.run([bash(), os.path.abspath(os.path.join(TOOLS, "build-release.sh")).replace("\\", "/")], cwd=d, env={**os.environ, "BUILD_DRY": "1"},
                          capture_output=True, text=True)


class Release(unittest.TestCase):
    def test_build_outside_clean_master_refused(self):
        d = tempfile.mkdtemp()
        git(d, "init", "-q", "-b", "feature")
        open(os.path.join(d, "a"), "w").write("1")
        git(d, "add", "."); git(d, "commit", "-qm", "x")
        self.assertEqual(build(d).returncode, 3)
        git(d, "checkout", "-qb", "master")
        open(os.path.join(d, "a"), "w").write("2")      # грязное дерево
        self.assertEqual(build(d).returncode, 3)
        git(d, "commit", "-qam", "y")
        r = build(d)
        self.assertEqual(r.returncode, 0, r.stderr)


class Sprawl(unittest.TestCase):
    def test_new_tk_script_warned_and_root_over_base_fails(self):
        d = tempfile.mkdtemp()
        git(d, "init", "-q", "-b", "master")
        open(os.path.join(d, "a"), "w").write("1")
        git(d, "add", "."); git(d, "commit", "-qm", "x")
        git(d, "checkout", "-qb", "t")
        os.makedirs(os.path.join(d, "tools", "compute"))
        open(os.path.join(d, "tools", "compute", "tk150-foo.sh"), "w").write("1")
        open(os.path.join(d, "tools", "sprawl-base.txt"), "w").write("root_files=0\n")
        git(d, "add", "."); git(d, "commit", "-qm", "y")
        w, f = S.check(d, "master")
        self.assertEqual(len(w), 1)
        self.assertEqual(len(f), 1)


if __name__ == "__main__":
    unittest.main()
