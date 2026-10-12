"""TK-133 С-44, С-43, С-45: упаковка / вытеснение / заморозка / бюджет alsched на модели sched_sim + причины и сбои состояния."""
import os, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import alsched as S
import sched_sim as SS

H = 3600
S.RESERVE_CORES = 0     # TK-219: модель упаковки считает все 16 ядер; резерв гейтов проверяет класс Reserve ниже


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


class Reserve(unittest.TestCase):
    """TK-219, В-229 п.10: prod --kind work не занимает 3 резервных ядра; gate/build берут их."""
    def run_case(self, kinds):
        S.RESERVE_CORES = 3
        try:
            be = SS.SimBE(); core = S.Core(be, ncpu=SS.NCPU, mem=56)
            for i, k in enumerate(kinds):
                j = SS.job("r%02d" % i, "r%d" % i, "prod", 1, 1, "none", 3600, 0)
                if k != "work":
                    j["kind"] = k
                core.add(j)
            for _ in range(6):
                core.tick(); be.advance(core, S.TICK)
            return {k: sum(1 for r in core.running("prod") if r.get("kind", "work") == k) for k in ("work", "gate")}
        finally:
            S.RESERVE_CORES = 0

    def test_work_leaves_three_cores(self):
        self.assertEqual(self.run_case(["work"] * 20)["work"], SS.NCPU - 3)

    def test_gate_takes_reserved_cores(self):
        r = self.run_case(["work"] * 20 + ["gate"] * 3)
        self.assertEqual((r["work"], r["gate"]), (SS.NCPU - 3, 3))


class ReserveFact(unittest.TestCase):
    """TK-220: допуск по факту — work резерв не трогает, build на 4 ядра при занятой work машине стартует за ≤ 1 тик."""
    def run_case(self, reserve):
        S.RESERVE_CORES = reserve
        try:
            be = SS.SimBE(); core = S.Core(be, ncpu=SS.NCPU, mem=56, pack="fact")
            for i in range(12):
                j = SS.job("f%02d" % i, "f%d" % i, "prod", 4, 2, "none", 7200, 0)
                core.add(j); be.cpu_rate[j["id"]] = 1
            for _ in range(180):
                core.tick(); be.advance(core, S.TICK)
            work = len(core.running("prod"))
            b = SS.job("b", "build", "prod", 4, 2, "none", 3600, be.t); b["kind"] = "build"; b["prio"] = 5
            core.add(b); be.cpu_rate["b"] = 1
            core.tick()
            return work, core.jobs["b"]["state"]
        finally:
            S.RESERVE_CORES = 0

    def test_build_starts_next_tick_work_capped(self):
        work, st = self.run_case(3)
        self.assertEqual(st, "running")
        self.assertLess(work, 11)             # без резерва по факту идут 11 (fact_case)


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


class Restart(unittest.TestCase):
    """TK-222: рестарт демона теряет frozen_for — reconcile возвращает метку по FreezerState, оттайка идёт штатно."""
    def test_frozen_without_mark_gets_hot_and_thaws(self):
        be = SS.SimBE(); core = S.Core(be, ncpu=SS.NCPU, mem=56)
        core.add(SS.job("a", "a", "prod", 8, 2, "none", 10 * H, 0, mr=5 * H))
        for _ in range(3):
            core.tick(); be.advance(core, S.TICK)
        be.freeze_unit("a")                               # демон «перезапущен»: заморозка есть, метки нет
        core2 = S.Core(be, ncpu=SS.NCPU, mem=56)
        core2.add(core.jobs["a"]); core2.jobs["a"].pop("frozen_for", None)
        core2.reconcile({u for u in ("a",) if be.is_frozen(u)})
        self.assertEqual(core2.jobs["a"].get("frozen_for"), "hot")

    def test_stale_hot_mark_dropped(self):
        be = SS.SimBE(); core = S.Core(be, ncpu=SS.NCPU, mem=56)
        core.add(SS.job("a", "a", "prod", 8, 2, "none", 10 * H, 0, mr=5 * H))
        core.tick()
        core.jobs["a"]["frozen_for"] = "hot"
        core.reconcile(set())
        self.assertNotIn("frozen_for", core.jobs["a"])


class Tk223(unittest.TestCase):
    """TK-223: оттайка по замеру задания, потолок и сигнал заморозки, rc отмены, предупреждение о kind."""
    def frozen_case(self, **kw):
        be = SS.SimBE(); be.psi = 2.0
        core = S.Core(be, ncpu=SS.NCPU, mem=56, pack="fact")
        j = SS.job("g", "tk216-gate2", "prod", 8, 2, "none", 7200, 0)
        j.update(state="running", t_start=0.0, cpus=list(range(8)), frozen_for="hot", **kw)
        core.add(j); be.jobs["g"] = dict(left=7200); be.fz.add("g")
        return be, core, j

    def test_thaw_by_own_measure_not_claim(self):
        be, core, j = self.frozen_case(frozen_need=1, frozen_t=0.0)
        core.guard_load(100.0, 0.8, True)          # 0,8·16 + 1 ≤ 0,9·16, а по заявке (8) не влезло бы
        self.assertNotIn("frozen_for", j)
        self.assertNotIn("g", be.fz)

    def test_claim_need_stays_frozen(self):
        be, core, j = self.frozen_case(frozen_t=0.0)
        core.guard_load(100.0, 0.8, True)
        self.assertEqual(j["frozen_for"], "hot")

    def test_freeze_ceiling_thaws_anyway(self):
        be, core, j = self.frozen_case(frozen_t=0.0)
        core.guard_load(S.FREEZE_CEIL_S + 1, 0.8, True)
        self.assertNotIn("frozen_for", j)

    def test_freeze_records_own_need(self):
        be = SS.SimBE(); be.psi = 60.0
        core = S.Core(be, ncpu=SS.NCPU, mem=62, pack="fact"); be.mem_av = 60.0
        for i in range(14):
            j = SS.job("p%02d" % i, "p%d" % i, "prod", 4, 2, "none", 7200, 0)
            core.add(j); be.cpu_rate[j["id"]] = 1.5
        for k in range(130):
            if k == 100:
                for i in be.cpu_rate:
                    be.cpu_rate[i] = 4
            core.tick(); be.advance(core, S.TICK)
        fz = [j for j in core.jobs.values() if j.get("frozen_for") == "hot"]
        self.assertTrue(fz and all(1 <= j["frozen_need"] <= j["cores"] and "frozen_t" in j for j in fz))

    def test_frozen_over_15_min_alerts_once(self):
        be, core, j = self.frozen_case(frozen_t=0.0)
        core.alert_frozen(S.FREEZE_ALERT_S + 1); core.alert_frozen(S.FREEZE_ALERT_S + 60)
        self.assertEqual(len([a for a in be.alerts if "ЗАМОРОЖЕНО" in a and "g" in a and "TK-216" in a]), 1)

    def test_queued_cancel_writes_rc(self):
        class Be(SS.SimBE):
            rcs = []
            def cancelled(self, j): return True
            def write_rc(self, j, rc): self.rcs.append((j["id"], rc))
        be = Be(); core = S.Core(be, ncpu=SS.NCPU, mem=56)
        core.add(SS.job("q", "tk065-wa", "prod", 2, 2, "none", 100, 0))
        core.tick()
        self.assertEqual(Be.rcs, [("q", -15)])

    def test_systemd_write_rc(self):
        with tempfile.TemporaryDirectory() as d:
            old = S.DIR; S.DIR = d
            try:
                S.SystemdBackend.write_rc(None, {"id": "x"}, -15)
                self.assertEqual(open(d + "/rc/x").read(), "-15\n")
            finally:
                S.DIR = old

    def test_gate_named_work_warns(self):
        S.RESERVE_CORES = 3
        try:
            for n in ("tk216-gate2", "tk204-gate3", "rpv-fastcheck", "tk1-build"):
                self.assertTrue(S.kind_warn(n, "work"), n)
            self.assertIsNone(S.kind_warn("tk216-gate2", "gate"))
            self.assertIsNone(S.kind_warn("tk212-f1b", "work"))
        finally:
            S.RESERVE_CORES = 0
