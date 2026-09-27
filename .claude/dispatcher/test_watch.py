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

    def test_end_to_end_posts_dispatcher_down(self):
        def fake_ssh(cmd, timeout=10.0):
            return True, "" if "ALERT" in cmd else "HOLD"
        posted = W.run_once(self.now, ssh_run=fake_ssh)  # state.json нет вовсе -> last_tick отсутствует
        self.assertTrue(any(f.kind == "dispatcher-down" for f in posted))


if __name__ == "__main__":
    unittest.main()
