"""Тесты сторожа CEO без модели (судья TK-002 п.2). По фикстуре на каждое условие (п.2д). stdlib
`unittest`, без сети (ssh — через инъекцию `ssh_run`, как просит сам watch.py)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch as D  # noqa: E402
import ticket as T  # noqa: E402
import watch as W  # noqa: E402

TZ = timezone(timedelta(hours=4))


def dt(s: str) -> datetime:
    return T.parse_dt(s)


class WatchSandbox(unittest.TestCase):
    """База: временные TICKETS_DIR/STATE_FILE/CEO_INBOX/heartbeat/watch-state — ничего боевого не трогаем."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.tickets_dir = base / "tickets"
        self.tickets_dir.mkdir(parents=True)
        self._orig = {
            "TICKETS_DIR": D.TICKETS_DIR, "STATE_FILE": D.STATE_FILE, "CEO_INBOX": D.CEO_INBOX,
            "CEO_WAKE_LOG": D.CEO_WAKE_LOG,
        }
        D.TICKETS_DIR = self.tickets_dir
        D.STATE_FILE = base / "state.json"
        D.CEO_INBOX = base / "ceo-inbox.md"
        D.CEO_WAKE_LOG = base / "ceo-wake.log"
        W.WATCH_STATE_FILE = base / "watch-state.json"
        W.WATCH_HEARTBEAT_FILE = base / "watch-heartbeat.json"
        self.now = dt("2026-09-27T12:00:00+04:00")

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(D, k, v)
        self.tmp.cleanup()


class DispatcherAliveTests(WatchSandbox):
    def test_no_last_tick_is_a_finding(self):
        findings = W.check_dispatcher_alive({}, self.now)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].kind, "dispatcher-down")

    def test_stale_last_tick_is_a_finding(self):
        state = {"last_tick": T.now_iso(self.now - timedelta(minutes=D.RUN_TIMEOUT))}
        findings = W.check_dispatcher_alive(state, self.now)
        self.assertEqual(len(findings), 1)

    def test_fresh_last_tick_is_silent(self):
        state = {"last_tick": T.now_iso(self.now - timedelta(seconds=30))}
        self.assertEqual(W.check_dispatcher_alive(state, self.now), [])


class BudgetWatchTests(WatchSandbox):
    def test_silent_when_nothing_exceeded(self):
        self.assertEqual(W.check_budgets({}, self.now), [])

    def test_daily_budget_exceeded(self):
        state = {"daily_cost": {D._today(self.now): D.DAILY_COST_USD + 1}}
        findings = W.check_budgets(state, self.now)
        self.assertTrue(any(f.key == "daily" for f in findings))

    def test_hour_budget_exceeded(self):
        state = {"cost_history": [[T.now_iso(self.now), D.HOUR_COST_USD + 1]]}
        findings = W.check_budgets(state, self.now)
        self.assertTrue(any(f.key == "hour" for f in findings))

    def test_ticket_budget_exceeded(self):
        state = {"ticket_budget": {"TK-1": 5.0}, "ticket_cost": {"TK-1": 6.0}}
        findings = W.check_budgets(state, self.now)
        self.assertTrue(any(f.key == "ticket:TK-1" for f in findings))


class BlockedNeedsOwnerTests(WatchSandbox):
    def test_blocked_and_needs_owner_are_findings(self):
        T.create_ticket(self.tickets_dir, owner="engineer", title="Застряла", status="todo")
        p2 = T.create_ticket(self.tickets_dir, owner="researcher", title="Ждёт владельца", status="todo")
        T.write_header_updates(p2, {"status": "needs_owner"})
        findings = W.check_blocked_and_needs_owner(self.now)
        kinds = {f.kind for f in findings}
        self.assertIn("needs_owner", kinds)

    def test_todo_is_silent(self):
        T.create_ticket(self.tickets_dir, owner="engineer", title="Обычная", status="todo")
        self.assertEqual(W.check_blocked_and_needs_owner(self.now), [])

    def test_unreadable_ticket_is_a_signal_not_silence(self):
        (self.tickets_dir / "BROKEN.md").write_text("нет шапки\n", encoding="utf-8")
        findings = W.check_blocked_and_needs_owner(self.now)
        self.assertTrue(any(f.kind == "ticket-unreadable" for f in findings))


class OrphanTicketTests(WatchSandbox):
    def test_stale_in_progress_is_orphan(self):
        p = T.create_ticket(self.tickets_dir, owner="engineer", title="Забытая", status="todo",
                             now=self.now - timedelta(hours=5))
        T.write_header_updates(p, {"status": "in_progress"}, now=self.now - timedelta(hours=5))
        T.append_log(p, "engineer", "начал", now=self.now - timedelta(hours=5))
        findings = W.check_orphan_tickets(self.now)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].kind, "orphan-ticket")

    def test_recent_in_progress_is_silent(self):
        p = T.create_ticket(self.tickets_dir, owner="engineer", title="Свежая", status="todo")
        T.write_header_updates(p, {"status": "in_progress"})
        T.append_log(p, "engineer", "начал", now=self.now - timedelta(minutes=5))
        self.assertEqual(W.check_orphan_tickets(self.now), [])

    def test_done_ticket_is_not_orphan_even_if_old(self):
        p = T.create_ticket(self.tickets_dir, owner="engineer", title="Готова", status="todo",
                             now=self.now - timedelta(hours=10))
        T.write_header_updates(p, {"status": "done"}, now=self.now - timedelta(hours=10))
        self.assertEqual(W.check_orphan_tickets(self.now), [])


class SteamDeckWatchTests(WatchSandbox):
    def test_alert_file_becomes_finding(self):
        def fake_ssh(cmd, timeout=10.0):
            if "ALERT" in cmd:
                return True, "ALERT-rework: 54 суток разобраны повторно"
            return True, ""
        findings = W.check_steam_deck(fake_ssh)
        self.assertTrue(any(f.kind == "deck-alert" for f in findings))

    def test_ssh_failure_is_a_signal_not_silence(self):
        """п.2в: ошибка ssh/чтения — сигнал, не молчание."""
        def fake_ssh(cmd, timeout=10.0):
            return False, "Connection timed out"
        findings = W.check_steam_deck(fake_ssh)
        self.assertTrue(any(f.kind == "deck-ssh-error" for f in findings))
        self.assertEqual(len(findings), 2)  # alerts-запрос и queue-запрос — оба упали

    def test_no_alerts_and_empty_queue_is_silent(self):
        def fake_ssh(cmd, timeout=10.0):
            if "ALERT" in cmd:
                return True, ""
            return True, "HOLD"
        self.assertEqual(W.check_steam_deck(fake_ssh), [])

    def test_idle_with_pending_queue_is_a_finding(self):
        def fake_ssh(cmd, timeout=10.0):
            if "ALERT" in cmd:
                return True, ""
            return True, f"3 {int(W.DECK_QUEUE_STALE_MINUTES * 60) + 120}"
        findings = W.check_steam_deck(fake_ssh)
        self.assertTrue(any(f.kind == "deck-idle" for f in findings))

    def test_active_queue_is_silent(self):
        def fake_ssh(cmd, timeout=10.0):
            if "ALERT" in cmd:
                return True, ""
            return True, "3 30"  # свежо
        self.assertEqual(W.check_steam_deck(fake_ssh), [])


class SshEncodingTests(WatchSandbox):
    def test_ssh_run_decodes_as_utf8(self):
        """CEO 27.09: вывод ssh шёл кракозябрами — subprocess.run без явной кодировки брал локальную
        (Windows-консоль), как уже исправлено для role_memory.py:deck_alert."""
        captured = {}
        orig_run = W.subprocess.run

        class FakeResult:
            returncode = 0
            stdout = "ALERT-rework: очередь застряла"
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return FakeResult()

        W.subprocess.run = fake_run
        try:
            W._ssh_run("echo test")
        finally:
            W.subprocess.run = orig_run
        self.assertEqual(captured.get("encoding"), "utf-8")
        self.assertEqual(captured.get("errors"), "replace")


class SshImmediateRetryTests(WatchSandbox):
    """CEO 27.09: разовый ssh-таймаут (23:59, 00:11), а сразу следом ssh отвечал за 0,44 с — один
    немедленный повтор внутри _ssh_run должен был отфильтровать это ещё до классификации находки."""

    def test_success_on_immediate_retry_counts_as_success(self):
        calls = []

        def fake_once(cmd_suffix, timeout=10.0):
            calls.append(cmd_suffix)
            if len(calls) == 1:
                return False, "Connection timed out"
            return True, "ok"

        orig = W._ssh_run_once
        W._ssh_run_once = fake_once
        try:
            ok, out = W._ssh_run("echo test")
        finally:
            W._ssh_run_once = orig
        self.assertTrue(ok)
        self.assertEqual(out, "ok")
        self.assertEqual(len(calls), 2)

    def test_failure_persists_after_retry_exhausted(self):
        def fake_once(cmd_suffix, timeout=10.0):
            return False, "Connection timed out"

        orig = W._ssh_run_once
        W._ssh_run_once = fake_once
        try:
            ok, out = W._ssh_run("echo test")
        finally:
            W._ssh_run_once = orig
        self.assertFalse(ok)

    def test_first_success_makes_no_retry_call(self):
        calls = []

        def fake_once(cmd_suffix, timeout=10.0):
            calls.append(cmd_suffix)
            return True, "ok"

        orig = W._ssh_run_once
        W._ssh_run_once = fake_once
        try:
            W._ssh_run("echo test")
        finally:
            W._ssh_run_once = orig
        self.assertEqual(len(calls), 1)


class SshFailStreakTests(WatchSandbox):
    """CEO 27.09: будить только после N ПОДРЯД неудачных ЦИКЛОВ; разовые/парные — в сводку."""

    def test_first_two_failures_are_transient_not_wake(self):
        ws = {}
        f = [W.Finding("deck-ssh-error", "alerts", "timeout")]
        out1 = W._apply_ssh_fail_streak(f, ws)
        self.assertEqual(out1[0].kind, "deck-ssh-error-transient")
        out2 = W._apply_ssh_fail_streak(f, ws)
        self.assertEqual(out2[0].kind, "deck-ssh-error-transient")

    def test_third_consecutive_failure_wakes(self):
        ws = {}
        f = [W.Finding("deck-ssh-error", "alerts", "timeout")]
        W._apply_ssh_fail_streak(f, ws)
        W._apply_ssh_fail_streak(f, ws)
        out3 = W._apply_ssh_fail_streak(f, ws)
        self.assertEqual(out3[0].kind, "deck-ssh-error")

    def test_success_in_between_resets_streak(self):
        ws = {}
        f_fail = [W.Finding("deck-ssh-error", "alerts", "timeout")]
        W._apply_ssh_fail_streak(f_fail, ws)
        W._apply_ssh_fail_streak(f_fail, ws)
        W._apply_ssh_fail_streak([], ws)  # цикл без ошибки — ssh снова отвечает
        out = W._apply_ssh_fail_streak(f_fail, ws)
        self.assertEqual(out[0].kind, "deck-ssh-error-transient", "счётчик должен был сброситься")

    def test_non_ssh_findings_are_untouched(self):
        ws = {}
        f = [W.Finding("orphan-ticket", "TK-1", "застряла")]
        out = W._apply_ssh_fail_streak(f, ws)
        self.assertEqual(out, f)

    def test_end_to_end_two_transient_cycles_go_to_summary_not_inbox(self):
        def fake_ssh_always_fails(cmd, timeout=10.0):
            return False, "Connection timed out"

        W.run_once(self.now, ssh_run=fake_ssh_always_fails)
        W.run_once(self.now + timedelta(minutes=2), ssh_run=fake_ssh_always_fails)
        inbox = D.CEO_INBOX.read_text(encoding="utf-8") if D.CEO_INBOX.exists() else ""
        self.assertNotIn("[watch-deck-ssh-error]", inbox)
        self.assertIn("watch-summary", inbox)

    def test_end_to_end_third_consecutive_cycle_wakes(self):
        def fake_ssh_always_fails(cmd, timeout=10.0):
            return False, "Connection timed out"

        for i in range(3):
            W.run_once(self.now + timedelta(minutes=2 * i), ssh_run=fake_ssh_always_fails)
        inbox = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertIn("watch-deck-ssh-error", inbox)


class SteamDeckHoldTests(WatchSandbox):
    """CEO 27.09: ALERT-idle-deck при активном HOLD — ожидаемое состояние (паузу ставит CEO по слову
    владельца) — не будить, а в сводку; другие тревоги (например ALERT-rework) под HOLD всё равно будят."""

    def test_idle_deck_alert_under_hold_goes_to_summary_kind(self):
        def fake_ssh(cmd, timeout=10.0):
            if "ALERT" in cmd:
                return True, "ALERT-idle-deck: очередь простаивает 18:26Z"
            return True, "HOLD"
        findings = W.check_steam_deck(fake_ssh)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].kind, "deck-idle-expected")

    def test_other_alert_under_hold_still_wakes(self):
        def fake_ssh(cmd, timeout=10.0):
            if "ALERT" in cmd:
                return True, "ALERT-rework: 54 суток разобраны повторно 18:26Z"
            return True, "HOLD"
        findings = W.check_steam_deck(fake_ssh)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].kind, "deck-alert")

    def test_idle_deck_alert_without_hold_still_wakes(self):
        """ALERT-idle-deck без HOLD — это уже НЕ ожидаемое состояние, будим как обычно."""
        def fake_ssh(cmd, timeout=10.0):
            if "ALERT" in cmd:
                return True, "ALERT-idle-deck: простаивает"
            return True, "NOHOLD"
        findings = W.check_steam_deck(fake_ssh)
        self.assertTrue(any(f.kind == "deck-alert" for f in findings))
        self.assertFalse(any(f.kind == "deck-idle-expected" for f in findings))

    def test_summary_kind_goes_out_as_batched_summary_not_bare_wake(self):
        """Формат — «watch-summary» пачкой, не отдельная срочная строка watch-deck-idle-expected."""
        ws = {}
        f = [W.Finding("deck-idle-expected", "ALERT-idle-deck", "простаивает 18:26Z")]
        W.notify_findings(f, ws, self.now)
        inbox = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertIn("watch-summary", inbox)
        self.assertNotIn("watch-deck-idle-expected", inbox)

    def test_second_occurrence_within_window_batches_not_immediate(self):
        ws = {}
        f1 = [W.Finding("deck-idle-expected", "ALERT-idle-deck", "простаивает 18:26Z")]
        W.notify_findings(f1, ws, self.now)  # первое — само задаёт точку отсчёта окна и уходит сразу
        before = D.CEO_INBOX.read_text(encoding="utf-8")
        f2 = [W.Finding("deck-idle-expected", "ALERT-idle-deck", "простаивает ДРУГАЯ ПРИЧИНА")]
        W.notify_findings(f2, ws, self.now + timedelta(minutes=5))
        after = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertEqual(before, after, "второе в течение окна должно копиться, не уходить немедленно")
        later = self.now + timedelta(hours=W.WATCH_SUMMARY_EVERY_HOURS, minutes=5)
        W.notify_findings(f2, ws, later)
        self.assertIn("ДРУГАЯ ПРИЧИНА", D.CEO_INBOX.read_text(encoding="utf-8"))


class ContentFingerprintDedupTests(WatchSandbox):
    """CEO 27.09: ALERT-rework перезаписывается каждые ~15 мин с той же сутью, новой меткой времени —
    сравнивать текст без времени, будить один раз, не на каждое перезаписывание."""

    def test_same_content_different_timestamp_is_not_reposted(self):
        ws = {}
        W.notify_findings([W.Finding("deck-alert", "ALERT-rework", "разобрано повторно 18:26Z")], ws, self.now)
        posted2 = W.notify_findings(
            [W.Finding("deck-alert", "ALERT-rework", "разобрано повторно 18:41Z")], ws,
            self.now + timedelta(minutes=15))
        self.assertEqual(posted2, [])

    def test_genuinely_different_content_reposts_immediately(self):
        ws = {}
        W.notify_findings([W.Finding("deck-alert", "ALERT-rework", "разобрано повторно 18:26Z")], ws, self.now)
        posted2 = W.notify_findings(
            [W.Finding("deck-alert", "ALERT-rework", "СОВСЕМ ДРУГАЯ ПРИЧИНА 18:41Z")], ws,
            self.now + timedelta(minutes=15))
        self.assertEqual(len(posted2), 1)

    def test_non_deck_kinds_unaffected_by_message_drift(self):
        """orphan-ticket/budget-watch сообщения естественно меняются (возраст, суммы) — это НЕ повод
        считать сигнал новым; сигнатура для них не участвует, только временное окно."""
        ws = {}
        W.notify_findings([W.Finding("orphan-ticket", "TK-1", "TK-1: без записи 2.0 ч")], ws, self.now)
        posted2 = W.notify_findings([W.Finding("orphan-ticket", "TK-1", "TK-1: без записи 2.3 ч")], ws,
                                     self.now + timedelta(minutes=15))
        self.assertEqual(posted2, [])

    def test_same_content_after_repeat_window_reposts_as_reminder(self):
        ws = {}
        f = [W.Finding("deck-alert", "ALERT-rework", "разобрано повторно 18:26Z")]
        W.notify_findings(f, ws, self.now)
        later = self.now + timedelta(hours=W.WATCH_DEDUP_REPEAT_HOURS, minutes=1)
        posted2 = W.notify_findings(f, ws, later)
        self.assertEqual(len(posted2), 1)


class DedupTests(WatchSandbox):
    def test_first_occurrence_posts(self):
        ws = {}
        posted = W.notify_findings([W.Finding("blocked", "TK-1", "TK-1: status=blocked")], ws, self.now)
        self.assertEqual(len(posted), 1)
        self.assertIn("TK-1", D.CEO_INBOX.read_text(encoding="utf-8"))

    def test_repeat_within_window_is_suppressed(self):
        ws = {}
        f = [W.Finding("blocked", "TK-1", "TK-1: status=blocked")]
        W.notify_findings(f, ws, self.now)
        posted2 = W.notify_findings(f, ws, self.now + timedelta(minutes=10))
        self.assertEqual(posted2, [])

    def test_repeat_after_window_reposts(self):
        ws = {}
        f = [W.Finding("blocked", "TK-1", "TK-1: status=blocked")]
        W.notify_findings(f, ws, self.now)
        later = self.now + timedelta(hours=W.WATCH_DEDUP_REPEAT_HOURS, minutes=1)
        posted2 = W.notify_findings(f, ws, later)
        self.assertEqual(len(posted2), 1)

    def test_resolved_finding_forgotten_so_recurrence_posts_immediately(self):
        ws = {}
        f = [W.Finding("blocked", "TK-1", "TK-1: status=blocked")]
        W.notify_findings(f, ws, self.now)
        W.notify_findings([], ws, self.now + timedelta(minutes=5))  # снято
        posted3 = W.notify_findings(f, ws, self.now + timedelta(minutes=6))  # снова — сразу, не ждём окна
        self.assertEqual(len(posted3), 1)


class RunOnceTests(WatchSandbox):
    def test_writes_heartbeat_even_with_no_findings(self):
        def fake_ssh(cmd, timeout=10.0):
            return True, "" if "ALERT" in cmd else "HOLD"
        W.run_once(self.now, ssh_run=fake_ssh)
        self.assertTrue(W.WATCH_HEARTBEAT_FILE.exists())

    def test_first_ever_cycle_no_last_tick_is_grace_not_finding(self):
        """CEO 27.09: «нет last_tick» сразу после старта — грация 2 интервала, не находка."""
        def fake_ssh(cmd, timeout=10.0):
            return True, "" if "ALERT" in cmd else "HOLD"
        posted = W.run_once(self.now, ssh_run=fake_ssh)  # state.json нет вовсе -> last_tick отсутствует
        self.assertFalse(any(f.kind == "dispatcher-down" for f in posted))

    def test_no_last_tick_after_grace_window_is_a_finding(self):
        def fake_ssh(cmd, timeout=10.0):
            return True, "" if "ALERT" in cmd else "HOLD"
        W.run_once(self.now, ssh_run=fake_ssh)  # первый цикл — задаёт started_at
        later = self.now + timedelta(minutes=D.POLL_INTERVAL / 60 * 3)  # заведомо за пределами 2×POLL_INTERVAL
        posted = W.run_once(later, ssh_run=fake_ssh)
        self.assertTrue(any(f.kind == "dispatcher-down" for f in posted))


if __name__ == "__main__":
    unittest.main()
