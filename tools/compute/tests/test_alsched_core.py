"""TK-133 С-44, С-43, С-45: упаковка / вытеснение / заморозка / бюджет alsched на модели sched_sim + причины и сбои состояния."""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import alsched as S
import sched_sim as SS

H = 3600


class Packing(unittest.TestCase):
    def test_wide_prio_overtakes_narrow_queue(self):
        w, _ = SS.wide_behind_narrow(3)
        self.assertLessEqual(w, 600 + 2 * S.TICK)

    def test_fifo_wide_raises_wait_alert(self):
        _, alerts = SS.wide_behind_narrow(5)
        self.assertGreaterEqual(len(alerts), 1)

    def test_claim_vs_fact_packing(self):
        self.assertEqual(SS.fact_case("claim"), 4)
        self.assertEqual(SS.fact_case("fact"), 11)

    def test_seq_disk_one_per_disk(self):
        self.assertEqual(SS.seq_case(), ["s0", "s2", "s3"])

    def test_memory_gate_keeps_all_cores(self):
        self.assertEqual(SS.mem_case(), 16)

    def test_honest_jobs_not_overcommitted(self):
        self.assertEqual(SS.honest_case(), ("queued", False))


class Preemption(unittest.TestCase):
    def test_head_preempts_narrow_and_retry_not_r1(self):
        ts, frozen, thaw, state, frozen_for = SS.preempt_case()
        self.assertIsNotNone(ts)
        self.assertLessEqual(ts, S.PREEMPT_S + 3 * S.TICK)
        self.assertEqual(frozen, {"n0", "retry"})
        self.assertTrue(thaw)
        self.assertEqual(state, "done")
        self.assertFalse(frozen_for)

    def test_equal_prio_does_not_freeze_bare_units(self):
        _, frozen, _, _, _ = SS.preempt_case(5)
        self.assertFalse(frozen - {"n0"})


class Freeze(unittest.TestCase):
    def test_hot_psi_calm_does_not_freeze(self):
        self.assertEqual(SS.hot_psi_case()[0], 0)

    def test_hot_psi_overload_freezes_bounded(self):
        n = SS.hot_psi_case()[1]
        self.assertTrue(1 <= n <= 150 // S.FACT_SETTLE_S + 1)

    def test_iso_end_retries_then_alerts(self):
        self.assertEqual(SS.iso_end_case(), (4, [], 9, 3))


class Budget(unittest.TestCase):
    def test_max_runtime_kills_with_reason(self):
        be = SS.SimBE(); core = S.Core(be, ncpu=SS.NCPU, mem=56)
        core.add(SS.job("long", "long", "prod", 2, 2, "none", 10 * H, 0, mr=1800))
        core.add(SS.job("w", "wave", "measure", SS.NCPU, 1, "none", 600, 0, mr=300))
        for _ in range(600):
            core.tick(); be.advance(core, S.TICK)
        self.assertEqual(set(be.killed), {"long", "w"})
        self.assertIn("max-runtime", core.jobs["long"]["kill_why"])
        self.assertIn("max-runtime", core.jobs["w"]["kill_why"])


class Reasons(unittest.TestCase):
    def test_queued_cancel_stores_reason(self):
        class Be(SS.SimBE):
            def cancelled(self, j): return True
            def cancel_why(self, j): return "перенос на 2-й проход"
        be = Be(); core = S.Core(be, ncpu=SS.NCPU, mem=56)
        core.add(SS.job("q", "tk065-wa", "prod", 2, 2, "none", 100, 0))
        core.tick()
        j = core.jobs["q"]
        self.assertEqual((j["state"], j["rc"]), ("done", -15))
        self.assertIn("снят из очереди", j["reason"])
        self.assertIn("перенос на 2-й проход", j["reason"])

    def test_kill_reason_prefixed_to_fail_reason(self):
        class Be(SS.SimBE):
            def fail_reason(self, j, rc): return "rc 124"
            def done(self, j): return 124 if self.jobs[j["id"]]["left"] <= 0 else None
        be = Be(); core = S.Core(be, ncpu=SS.NCPU, mem=56)
        core.add(SS.job("a", "tk048-x", "prod", 2, 2, "none", 10 * H, 0, mr=600))
        for _ in range(400):
            core.tick(); be.advance(core, S.TICK)
        r = core.jobs["a"].get("reason", "")
        self.assertTrue(r.startswith("снят демоном: бюджет max-runtime"), r)
        self.assertTrue(r.endswith("rc 124"), r)

    def test_cancel_cli_writes_reason_file(self):
        d = tempfile.mkdtemp()
        old = S.DIR
        S.DIR = d
        argv = sys.argv
        sys.argv = ["alsched.py", "cancel", "j1", "дубль", "прохода"]
        try:
            self.assertEqual(S.main(), 0)
            self.assertEqual(open(os.path.join(d, "cancel", "j1"), encoding="utf-8").read().strip(), "дубль прохода")
        finally:
            S.DIR = old
            sys.argv = argv


class StateFailures(unittest.TestCase):
    def test_warn_once_writes_alerts_log_once(self):
        d = tempfile.mkdtemp()
        old, S.DIR = S.DIR, d
        S._WARNED.discard("k-test")
        try:
            S.warn_once("k-test", "первый")
            S.warn_once("k-test", "второй")
            lines = open(os.path.join(d, "alerts.log"), encoding="utf-8").read().splitlines()
        finally:
            S.DIR = old
        self.assertEqual(len(lines), 1)
        self.assertIn("СБОЙ СОСТОЯНИЯ: первый", lines[0])

    def test_alert_line_survives_unwritable_dir(self):
        old, S.DIR = S.DIR, os.path.join(tempfile.mkdtemp(), "нет", "такого")
        try:
            S.alert_line("x")        # не падает: stderr
        finally:
            S.DIR = old

    def test_no_bare_except_pass_left(self):
        import re
        src = open(os.path.join(os.path.dirname(__file__), "..", "alsched.py"), encoding="utf-8").read()
        for m in re.finditer(r"except [^\n]*:\n\s+pass\n", src):
            line = src.count("\n", 0, m.start()) + 1
            self.fail(f"alsched.py:{line}: except … pass без записи")


if __name__ == "__main__":
    unittest.main()
