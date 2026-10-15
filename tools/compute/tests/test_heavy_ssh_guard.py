"""TK-240: замок тяжёлых ssh-команд на сервере счёта (.claude/hooks/heavy_ssh_guard.py)."""
import importlib.util, json, os, subprocess, sys, unittest

HOOK = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".claude", "hooks", "heavy_ssh_guard.py")
spec = importlib.util.spec_from_file_location("heavy_ssh_guard", HOOK)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)

SSH = "ssh -i k -o UserKnownHostsFile=h root@89.163.242.211 "


class Denied(unittest.TestCase):
    def deny(self, cmd):
        self.assertTrue(G.verdict(cmd), cmd)

    def test_find_du_rsync(self):
        self.deny(SSH + "'find /data -name x'")
        self.deny(SSH + "du -sh /data")
        self.deny("ssh calc 'rsync -a /data/a /data/b'")
        self.deny("ssh calc find /data")

    def test_recursive_and_tar(self):
        self.deny(SSH + "'ls -R /data'")
        self.deny(SSH + "'grep -r foo /data/alpha'")
        self.deny(SSH + "'cp -r /data/a /data/b'")
        self.deny(SSH + "'tar czf /tmp/x.tgz /data/a'")

    def test_hash_and_cat(self):
        self.deny(SSH + "'sha256sum /data/alpha/*.binlog'")
        self.deny(SSH + "'md5sum /data/a/x.abin'")
        self.deny(SSH + "'cat /data/alpha/x.binlog'")

    def test_python(self):
        self.deny(SSH + "python3 /data/tk0/a.py")
        self.deny(SSH + "python3 -m http.server")
        self.deny(SSH + "\"python3 -c '" + "x" * 250 + "'\"")

    def test_nested(self):
        self.deny(SSH + "\"cd /data && ls | head; find /data/alpha -type f\"")
        self.deny(SSH + "'bash -c \"du -sh /data\"'")
        self.deny(SSH + "'echo $(find /data)'")
        self.deny(SSH + "'D=/data; find $D'")
        self.deny(SSH + "'sudo nice -n 5 find /data'")
        self.deny(SSH + "'ls /data | xargs md5sum'")
        self.deny("for i in 1 2; do ssh calc find /data; done")

    def test_heredoc(self):
        self.deny("ssh calc bash -s <<'EOF'\nfind /data\nEOF")
        self.deny("ssh calc <<'EOF'\ndu -sh /data\nEOF")
        self.deny("ssh calc python3 - <<'EOF'\n" + "x = 1\n" * 100 + "EOF")
        self.deny("ssh calc python3 <<'EOF'\n" + "x = 1\n" * 100 + "EOF")

    def test_unparsable_falls_back(self):
        self.deny(SSH + "'find /data")                      # незакрытая кавычка


class Passed(unittest.TestCase):
    def ok(self, cmd):
        self.assertIsNone(G.verdict(cmd), cmd)

    def test_unit_and_wrapper(self):
        self.ok(SSH + "'systemd-run --unit x --collect -p CPUQuota=100% find /data'")
        self.ok(SSH + "systemd-run --unit x python3 /data/tk0/a.py")
        self.ok(SSH + "/data/benchrun.sh stand find /data")
        self.ok(SSH + "'/data/benchrun.sh wave du -sh /data'")

    def test_alsched(self):
        for v in ("submit --kind gate --cores 3 -- bash x.sh", "ps", "cancel 12", "wave", "stand", "reprio 1 2", "thaw"):
            self.ok(SSH + "python3 /data/sched/alsched.py " + v)
        self.ok(SSH + "'python3 /data/sched/alsched.py submit --cls prod -- find /data'")
        self.deny_other_alsched()

    def deny_other_alsched(self):
        self.assertTrue(G.verdict(SSH + "python3 /data/sched/alsched.py evil"))

    def test_light(self):
        for c in ("tail -n 50 /data/sched/logs/1.log", "cat /data/sched/jobs/1.json", "ls -l /data/alpha", "uptime",
                  "systemctl status alpha-x", "sha256sum /data/a/x.csv", "grep foo /data/x.log",
                  "python3 -m py_compile /data/a.py", "python3 -c 'print(1)'", "cat > /data/a.sh && mv /data/a.sh /data/b.sh",
                  "head -c 100 /data/alpha/x.csv", "df -h", "tar xzf - -C /data/x"):
            self.ok(SSH + f"'{c}'")

    def test_pipes_and_heredoc(self):
        self.ok("tar -cf - -C tools/pulse/web a b | " + SSH + "'tar -xf - -C /opt/alpha-board'")
        self.ok(SSH + "'cat > /data/a.sh' <<'EOF'\nfind /data\nEOF")
        self.ok(SSH + "python3 - <<'EOF'\nprint(1)\nEOF")

    def test_not_calc_or_not_ssh(self):
        self.ok("ssh root@13.140.29.171 'find /data'")
        self.ok("ssh vps 'du -sh /opt'")
        self.ok("find /data")
        self.ok("git commit -m 'ssh calc find /data'")
        self.ok("echo ssh calc find /data")
        self.ok("ssh -i k root@89.163.242.211")


class Hook(unittest.TestCase):
    def run_hook(self, cmd, tool="Bash"):
        p = subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_name": tool, "tool_input": {"command": cmd}}),
                           capture_output=True, text=True, encoding="utf-8")
        return p.returncode, p.stderr

    def test_exit_codes(self):
        rc, err = self.run_hook(SSH + "'find /data'")
        self.assertEqual(rc, 2)
        self.assertIn("benchrun.sh stand", err)
        self.assertEqual(self.run_hook(SSH + "'find /data'", tool="PowerShell")[0], 2)
        self.assertEqual(self.run_hook(SSH + "'find /data'", tool="Read")[0], 0)
        self.assertEqual(self.run_hook(SSH + "'systemd-run --unit x find /data'")[0], 0)
        self.assertEqual(self.run_hook(SSH + "'tail /data/x.log'")[0], 0)
        self.assertEqual(self.run_hook("")[0], 0)


if __name__ == "__main__":
    unittest.main()
