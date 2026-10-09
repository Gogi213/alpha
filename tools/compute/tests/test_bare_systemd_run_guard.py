"""TK-133 С-49: страж отказывает голому `systemd-run … CPUQuota` на сервере счёта."""
import importlib.util, io, json, os, subprocess, sys, unittest

HOOK = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".claude", "hooks", "bare_systemd_run.py")
spec = importlib.util.spec_from_file_location("bare_systemd_run", HOOK)
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)

SSH = "ssh -i k root@89.163.242.211 "


class Verdict(unittest.TestCase):
    def setUp(self):
        os.environ.pop("ALPHA_BARE_SYSTEMD_RUN_OK", None)

    def test_bare_denied(self):
        self.assertTrue(G.verdict(SSH + "'systemd-run --unit x -p CPUQuota=400% bash /data/tk0/a.sh'"))
        self.assertTrue(G.verdict("ssh calc systemd-run -p CPUQuota=100% foo"))

    def test_submit_and_benchrun_ok(self):
        self.assertIsNone(G.verdict(SSH + "python3 /data/sched/alsched.py submit --cls prod -- systemd-run -p CPUQuota=1%"))
        self.assertIsNone(G.verdict(SSH + "/data/benchrun.sh stand systemd-run -p CPUQuota=100% x"))

    def test_other_cases_pass(self):
        self.assertIsNone(G.verdict(SSH + "systemd-run --unit x bash y"))          # без CPUQuota
        self.assertIsNone(G.verdict("systemd-run -p CPUQuota=100% x"))             # не на сервере счёта
        self.assertIsNone(G.verdict("git commit -m 'systemd-run CPUQuota'"))

    def test_env_override(self):
        os.environ["ALPHA_BARE_SYSTEMD_RUN_OK"] = "1"
        self.assertIsNone(G.verdict(SSH + "systemd-run -p CPUQuota=100% x"))

    def test_hook_exit_codes(self):
        def run(cmd, tool="Bash"):
            p = subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_name": tool, "tool_input": {"command": cmd}}),
                               capture_output=True, text=True, encoding="utf-8")
            return p.returncode
        self.assertEqual(run(SSH + "systemd-run -p CPUQuota=100% x"), 2)
        self.assertEqual(run(SSH + "ls"), 0)
        self.assertEqual(run(SSH + "systemd-run -p CPUQuota=100% x", tool="Read"), 0)


if __name__ == "__main__":
    unittest.main()
