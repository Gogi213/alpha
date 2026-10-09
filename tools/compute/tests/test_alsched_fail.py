"""TK-117 К2.1: причина падения задания alsched и номер тикета из имени."""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import importlib.util
spec = importlib.util.spec_from_file_location("alsched", os.path.join(os.path.dirname(__file__), "..", "alsched.py"))
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


class FailReason(unittest.TestCase):
    def test_oom_kill_from_journal(self):
        j = "tk0s-tk115-wa-1.service: Failed with result 'oom-kill'.\n"
        self.assertIn("oom-kill", A.classify_fail(143, j))

    def test_timeout_and_signal(self):
        self.assertIn("timeout", A.classify_fail(143, "x.service: Failed with result 'timeout'."))
        self.assertEqual(A.classify_fail(137, "x.service: Failed with result 'signal'."), "signal")

    def test_exit_code(self):
        self.assertEqual(A.classify_fail(2, "x.service: Failed with result 'exit-code'."), "exit-code 2")

    def test_no_journal(self):
        self.assertIn("не найдена", A.classify_fail(143, ""))
        self.assertIn("демоном", A.classify_fail(124, ""))

    def test_ticket_of(self):
        self.assertEqual(A.ticket_of("tk115-wa-2026-02-05"), "TK-115")
        self.assertEqual(A.ticket_of("tk048-mplan2"), "TK-048")
        self.assertEqual(A.ticket_of("bench"), "")


class TickAlert(unittest.TestCase):
    def _run(self, rc):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        import sched_sim as SS
        class Be(SS.SimBE):
            def done(self, j): return rc if self.jobs[j["id"]]["left"] <= 0 else None
            def fail_reason(self, j, r): return "oom-kill"
        be = Be(); core = A.Core(be)
        core.add(SS.job("1", "tk115-wa-x", "prod", 2, 4, 0, 10, 0))
        core.tick()
        be.advance(core, 20)
        core.tick()
        return be, core.jobs["1"]

    def test_nonzero_rc_alerts_and_stores_reason(self):
        be, j = self._run(143)
        self.assertEqual((j["state"], j["rc"], j["reason"]), ("done", 143, "oom-kill"))
        self.assertTrue(any("УПАЛО" in a and "TK-115" in a and "oom-kill" in a for a in be.alerts), be.alerts)

    def test_zero_rc_silent(self):
        be, j = self._run(0)
        self.assertNotIn("reason", j)
        self.assertFalse(any("УПАЛО" in a for a in be.alerts))


if __name__ == "__main__":
    unittest.main()
