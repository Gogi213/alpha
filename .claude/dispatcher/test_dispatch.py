"""Юнит-тесты диспетчера — без сети, без настоящего `claude`. stdlib `unittest`.

Запуск:
    python -m unittest discover -s ".claude/dispatcher" -p "test_dispatch.py" -v
или из каталога `.claude/dispatcher`:
    python -m unittest test_dispatch -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch as D  # noqa: E402
import ticket as T  # noqa: E402

TZ = timezone(timedelta(hours=4))  # GMT+4 (память проекта)


def dt(s: str) -> datetime:
    return T.parse_dt(s)


class TicketParsingTests(unittest.TestCase):
    def test_parse_header_and_log(self):
        text = textwrap.dedent("""\
            ---
            id: TK-001
            title: Пример задачи
            owner: researcher
            status: todo
            reviewer: judge
            wait_for:
            updated: 2026-09-27T10:00:00+04:00
            ---

            Описание задачи, свободный текст.

            ## Лог

            ### 2026-09-27T10:05:00+04:00 researcher
            Сделал шаг раз. Дальше — шаг два. @judge посмотри план.

            ### 2026-09-27T10:10:00+04:00 judge
            Ок, план принят.
            """)
        tkt = T.parse_text(text, Path("TK-001.md"))
        self.assertEqual(tkt.id, "TK-001")
        self.assertEqual(tkt.owner, "researcher")
        self.assertEqual(tkt.status, "todo")
        self.assertEqual(tkt.reviewer, "judge")
        self.assertEqual(tkt.header.get("wait_for"), "")
        self.assertIn("Описание задачи", tkt.description)
        self.assertEqual(len(tkt.log), 2)
        self.assertEqual(tkt.log[0].author, "researcher")
        self.assertEqual(tkt.log[0].mentions, {"judge"})
        self.assertEqual(tkt.log[1].author, "judge")
        self.assertTrue(tkt.logged_since("judge", dt("2026-09-27T10:06:00+04:00")))
        self.assertFalse(tkt.logged_since("judge", dt("2026-09-27T10:11:00+04:00")))

    def test_no_header_raises(self):
        with self.assertRaises(ValueError):
            T.parse_text("нет шапки тут")

    def test_no_log_section_is_empty(self):
        text = ("---\nid: TK-002\nowner: engineer\nstatus: todo\nupdated: 2026-09-27T10:00:00+04:00\n---\n\n"
                "Описание.\n")
        tkt = T.parse_text(text, Path("TK-002.md"))
        self.assertEqual(tkt.log, [])
        self.assertEqual(tkt.description, "Описание.")


class TicketMutationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_next_id_write_header_append_log(self):
        p1 = T.create_ticket(self.dir, owner="researcher", title="Первая", now=dt("2026-09-27T09:00:00+04:00"))
        self.assertEqual(p1.stem, "TK-001")
        p2 = T.create_ticket(self.dir, owner="engineer", title="Вторая", now=dt("2026-09-27T09:00:00+04:00"))
        self.assertEqual(p2.stem, "TK-002")

        T.write_header_updates(p1, {"status": "waiting", "wait_for": "file:foo.txt"},
                                now=dt("2026-09-27T09:05:00+04:00"))
        tkt = T.read_ticket(p1)
        self.assertEqual(tkt.status, "waiting")
        self.assertEqual(tkt.header["wait_for"], "file:foo.txt")
        self.assertEqual(tkt.header["updated"], "2026-09-27T09:05:00+04:00")
        self.assertEqual(tkt.header["id"], "TK-001")  # прочие строки шапки не тронуты

        T.append_log(p1, "researcher", "Сделал X. @judge глянь.", now=dt("2026-09-27T09:10:00+04:00"))
        tkt = T.read_ticket(p1)
        self.assertEqual(len(tkt.log), 1)
        self.assertEqual(tkt.log[0].mentions, {"judge"})

    def test_next_id_survives_gaps(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "TK-001.md").write_text("x", encoding="utf-8")
        (self.dir / "TK-007.md").write_text("x", encoding="utf-8")
        self.assertEqual(T.next_ticket_id(self.dir), "TK-008")


class DispatchDecisionTests(unittest.TestCase):
    """Только `decide()` — правила (а)-(г), без запуска процессов."""

    def setUp(self):
        self.state = {}
        self.now = dt("2026-09-27T12:00:00+04:00")

    def ticket_from(self, text):
        return T.parse_text(text, Path("TK-x.md"))

    def test_todo_wakes_owner(self):
        text = "---\nid: TK-1\nowner: researcher\nstatus: todo\nupdated: 2026-09-27T11:00:00+04:00\n---\n\n## Лог\n"
        dec = D.decide(self.ticket_from(text), self.state, self.now)
        self.assertEqual((dec.role, dec.reason), ("researcher", "todo"))

    def test_mention_wakes_role_regardless_of_status(self):
        text = ("---\nid: TK-2\nowner: researcher\nstatus: in_progress\nupdated: 2026-09-27T11:00:00+04:00\n---\n\n"
                "## Лог\n\n### 2026-09-27T11:05:00+04:00 researcher\nНужна проверка. @judge глянь план.\n")
        dec = D.decide(self.ticket_from(text), self.state, self.now)
        self.assertEqual((dec.role, dec.reason), ("judge", "mention"))

    def test_mention_not_repeated_after_wake(self):
        text = ("---\nid: TK-2\nowner: researcher\nstatus: in_progress\nupdated: 2026-09-27T11:00:00+04:00\n---\n\n"
                "## Лог\n\n### 2026-09-27T11:05:00+04:00 researcher\n@judge глянь план.\n")
        self.state.setdefault("sessions", {})["TK-2::judge"] = {"last_woken": "2026-09-27T11:06:00+04:00"}
        dec = D.decide(self.ticket_from(text), self.state, self.now)
        self.assertIsNone(dec)

    def test_waiting_file_condition_met(self):
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "done.flag"
            marker.write_text("x", encoding="utf-8")
            text = (f"---\nid: TK-3\nowner: engineer\nstatus: waiting\nwait_for: file:{marker}\n"
                    "updated: 2026-09-27T11:00:00+04:00\n---\n\n## Лог\n")
            dec = D.decide(self.ticket_from(text), self.state, self.now)
            self.assertEqual((dec.role, dec.reason), ("engineer", "wait_for-met"))

    def test_waiting_file_condition_not_met(self):
        text = ("---\nid: TK-4\nowner: engineer\nstatus: waiting\nwait_for: file:/no/such/file\n"
                "updated: 2026-09-27T11:00:00+04:00\n---\n\n## Лог\n")
        dec = D.decide(self.ticket_from(text), self.state, self.now)
        self.assertIsNone(dec)

    def test_waiting_deck_condition_uses_check_wait_for(self):
        text = ("---\nid: TK-5\nowner: engineer\nstatus: waiting\nwait_for: deck:/home/deck/done\n"
                "updated: 2026-09-27T11:00:00+04:00\n---\n\n## Лог\n")
        orig = D.check_wait_for
        D.check_wait_for = lambda spec: spec == "deck:/home/deck/done"
        try:
            dec = D.decide(self.ticket_from(text), self.state, self.now)
        finally:
            D.check_wait_for = orig
        self.assertEqual((dec.role, dec.reason), ("engineer", "wait_for-met"))

    def test_done_with_reviewer_and_no_reviewer_entry_sets_in_review(self):
        text = ("---\nid: TK-6\nowner: researcher\nstatus: done\nreviewer: judge\n"
                "updated: 2026-09-27T11:00:00+04:00\n---\n\n## Лог\n\n"
                "### 2026-09-27T10:59:00+04:00 researcher\nГотово, прошу проверку.\n")
        dec = D.decide(self.ticket_from(text), self.state, self.now)
        self.assertEqual((dec.role, dec.reason), ("judge", "review"))
        self.assertEqual(dec.header_updates, {"status": "in_review"})

    def test_done_with_reviewer_reply_after_update_does_not_rewake(self):
        text = ("---\nid: TK-7\nowner: researcher\nstatus: done\nreviewer: judge\n"
                "updated: 2026-09-27T11:00:00+04:00\n---\n\n## Лог\n\n"
                "### 2026-09-27T11:05:00+04:00 judge\nПроверил, принято.\n")
        dec = D.decide(self.ticket_from(text), self.state, self.now)
        self.assertIsNone(dec)

    def test_in_progress_without_mention_is_idle(self):
        text = ("---\nid: TK-8\nowner: researcher\nstatus: in_progress\nupdated: 2026-09-27T11:00:00+04:00\n---\n\n"
                "## Лог\n\n### 2026-09-27T11:05:00+04:00 researcher\nРаботаю дальше.\n")
        dec = D.decide(self.ticket_from(text), self.state, self.now)
        self.assertIsNone(dec)


# --- фейковый «claude» для сквозных тестов --------------------------------------------------
#
# dispatch._popen() — единственная точка, где создаётся подпроцесс; тесты подменяют её, чтобы
# вместо настоящего CLAUDE_BIN запускать `<sys.executable> <fake_claude.py>` с теми же аргументами
# (-p <prompt> --output-format json ...). Каталог тикетов фейковый скрипт берёт из переменной
# окружения FAKE_TICKETS_DIR (её пробрасывает launch_run через env=dict(os.environ)).

FAKE_BIN_OK = r"""
import json, os, re, sys
from pathlib import Path
TICKETS_DIR = Path(os.environ["FAKE_TICKETS_DIR"])
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
tid = re.search(r"tickets/([\w-]+)\.md", prompt).group(1)
role = next((c for c in ("researcher", "engineer", "judge")
             if prompt.startswith(f"Ты — {c} ")), "unknown")
path = TICKETS_DIR / f"{tid}.md"
text = path.read_text(encoding="utf-8")
text = re.sub(r"(?m)^status:.*$", "status: done", text, count=1)  # роль сама правит шапку
if not text.endswith("\n"):
    text += "\n"
if "## Лог" not in text:
    text += "\n## Лог\n"
text += f"\n### 2099-01-01T00:00:00+04:00 {role}\nСделал шаг (фейковый прогон). status: done.\n"
path.write_text(text, encoding="utf-8")
print(json.dumps({"session_id": f"sess-{tid}-{role}", "total_cost_usd": 0.01,
                   "usage": {"input_tokens": 10, "output_tokens": 5}}))
"""

FAKE_BIN_SILENT = r"""
import json
print(json.dumps({"session_id": "sess-silent", "total_cost_usd": 0.0}))
"""

# Пишет каждый вызов в calls.jsonl (tid, role, resumed session_id или None, prompt) — для проверки
# SESSION_SCOPE (одна сессия на роль vs на задачу) и ротации по ALPHA_DISPATCH_ROTATE_TOKENS.
# Размер контекста ответа берёт из FAKE_CTX_TOKENS (по умолчанию 10).
FAKE_BIN_RECORD = r"""
import json, os, re, sys
from pathlib import Path
TICKETS_DIR = Path(os.environ["FAKE_TICKETS_DIR"])
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
tid = re.search(r"tickets/([\w-]+)\.md", prompt).group(1)
role = next((c for c in ("researcher", "engineer", "judge") if prompt.startswith(f"Ты — {c} ")), "unknown")
resume_idx = args.index("--resume") if "--resume" in args else None
resumed = args[resume_idx + 1] if resume_idx is not None else None
with (TICKETS_DIR.parent / "calls.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"tid": tid, "role": role, "resumed": resumed, "prompt": prompt}) + "\n")
path = TICKETS_DIR / f"{tid}.md"
text = path.read_text(encoding="utf-8")
text = re.sub(r"(?m)^status:.*$", "status: done", text, count=1)  # роль сама правит шапку
if not text.endswith("\n"):
    text += "\n"
if "## Лог" not in text:
    text += "\n## Лог\n"
text += f"\n### 2099-01-01T00:00:00+04:00 {role}\nШаг. status: done.\n"
path.write_text(text, encoding="utf-8")
ctx = int(os.environ.get("FAKE_CTX_TOKENS", "10"))
print(json.dumps({"session_id": f"sess-{role}-{tid}", "total_cost_usd": 0.01,
                   "usage": {"input_tokens": ctx}}))
"""


class DispatchRunTests(unittest.TestCase):
    """Сквозные тесты `tick()` с подменённым `CLAUDE_BIN` (без сети и без настоящего claude)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.tickets_dir = self.base / "tickets"
        self.tickets_dir.mkdir(parents=True)
        self.dispatcher_dir = self.base / "dispatcher"
        self.dispatcher_dir.mkdir(parents=True)

        self._orig = {k: getattr(D, k) for k in
                      ("TICKETS_DIR", "PROJECT_ROOT", "STATE_FILE", "RUNS_DIR", "RUNS_LOG",
                       "CEO_INBOX", "CLAUDE_BIN", "MAX_PARALLEL", "RUN_TIMEOUT")}
        D.TICKETS_DIR = self.tickets_dir
        D.PROJECT_ROOT = self.base
        D.STATE_FILE = self.dispatcher_dir / "state.json"
        D.RUNS_DIR = self.dispatcher_dir / "runs"
        D.RUNS_LOG = self.dispatcher_dir / "runs.log"
        D.CEO_INBOX = self.dispatcher_dir / "ceo-inbox.md"
        D.RUNNING.clear()
        self._orig_popen = D._popen
        os.environ["FAKE_TICKETS_DIR"] = str(self.tickets_dir)

    def tearDown(self):
        for tid, info in list(D.RUNNING.items()):
            try:
                info["popen"].kill()
                info["popen"].wait(timeout=5)
            except Exception:
                pass
            for fh in (info.get("out_fh"), info.get("err_fh")):
                try:
                    fh.close()
                except Exception:
                    pass
        D.RUNNING.clear()
        D._popen = self._orig_popen
        del os.environ["FAKE_TICKETS_DIR"]
        for k, v in self._orig.items():
            setattr(D, k, v)
        self.tmp.cleanup()

    def set_fake_bin(self, body: str) -> Path:
        """Пишет фейковый `claude` и подменяет `D._popen`, чтобы CLAUDE_BIN подменялся на python-скрипт."""
        script = self.base / "fake_claude.py"
        script.write_text(body, encoding="utf-8")

        def fake_popen(cmd, **kwargs):
            return subprocess.Popen([sys.executable, str(script)] + list(cmd[1:]), **kwargs)

        D._popen = fake_popen
        return script

    def wait_running(self, timeout=10.0):
        deadline = time.time() + timeout
        while D.RUNNING and time.time() < deadline:
            state = D.load_state()
            D._poll_running(state, datetime.now().astimezone())
            D.save_state(state)
            time.sleep(0.05)
        self.assertFalse(D.RUNNING, "фейковый прогон не завершился за отведённое время")

    def test_todo_ticket_runs_logs_and_saves_session(self):
        self.set_fake_bin(FAKE_BIN_OK)
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Тест",
                                now=dt("2026-09-27T12:00:00+04:00"))
        n = D.tick()
        self.assertEqual(n, 1)
        self.assertIn(path.stem, D.RUNNING)
        self.wait_running()

        tkt = T.read_ticket(path)
        self.assertEqual(len(tkt.log), 1)
        self.assertEqual(tkt.log[0].author, "researcher")

        state = D.load_state()
        self.assertEqual(state["ticket_sessions"][f"{path.stem}::researcher"]["session_id"],
                          f"sess-{path.stem}-researcher")
        self.assertTrue(D.RUNS_LOG.exists())
        self.assertIn(path.stem, D.RUNS_LOG.read_text(encoding="utf-8"))

    def test_no_log_entry_retries_once_then_blocks(self):
        self.set_fake_bin(FAKE_BIN_SILENT)
        path = T.create_ticket(self.tickets_dir, owner="engineer", title="Молчун",
                                now=dt("2026-09-27T12:00:00+04:00"))
        D.tick()
        self.wait_running()  # первая попытка молчит → внутри неё должен стартовать повтор
        # если повтор ещё не успел завершиться к моменту выхода из wait_running — исключение сработало бы там;
        # раз дошли сюда, все попытки для этого тикета исчерпаны и RUNNING пуст.
        tkt = T.read_ticket(path)
        self.assertEqual(tkt.status, "blocked")
        self.assertTrue(D.CEO_INBOX.exists())
        inbox = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertIn(path.stem, inbox)
        self.assertIn("blocked", inbox)

    def test_max_parallel_respected(self):
        self.set_fake_bin(FAKE_BIN_SILENT)
        D.MAX_PARALLEL = 1
        T.create_ticket(self.tickets_dir, owner="researcher", title="Первая", now=dt("2026-09-27T12:00:00+04:00"))
        T.create_ticket(self.tickets_dir, owner="engineer", title="Вторая", now=dt("2026-09-27T12:00:00+04:00"))
        D.tick()
        self.assertEqual(len(D.RUNNING), 1)

    def test_ticket_scope_allows_parallel_same_role(self):
        """researcher/engineer — scope "ticket": разные задачи одной роли не сериализуются."""
        self.set_fake_bin(FAKE_BIN_SILENT)
        D.MAX_PARALLEL = 2
        T.create_ticket(self.tickets_dir, owner="researcher", title="Первая")
        T.create_ticket(self.tickets_dir, owner="researcher", title="Вторая")
        D.tick()
        self.assertEqual(len(D.RUNNING), 2)

    def test_role_scope_serializes_and_shares_session(self):
        """judge — scope "role": не больше одной активной сессии сразу, вторая задача продолжает ту же сессию."""
        self.set_fake_bin(FAKE_BIN_RECORD)
        os.environ["FAKE_CTX_TOKENS"] = "10"
        self.addCleanup(lambda: os.environ.pop("FAKE_CTX_TOKENS", None))
        D.MAX_PARALLEL = 2
        T.create_ticket(self.tickets_dir, owner="judge", title="Проверка A")
        T.create_ticket(self.tickets_dir, owner="judge", title="Проверка B")

        D.tick()
        self.assertEqual(len(D.RUNNING), 1, "SESSION_SCOPE[judge]=role — вторая задача не должна стартовать сразу")
        self.wait_running()

        D.tick()
        self.assertEqual(len(D.RUNNING), 1)
        self.wait_running()

        calls = [json.loads(l) for l in (self.tickets_dir.parent / "calls.jsonl").read_text(encoding="utf-8")
                 .splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]["tid"], calls[1]["tid"], "вторая задача должна была дождаться своей очереди")
        self.assertIsNone(calls[0]["resumed"], "первый запуск роли — новая сессия, без --resume")
        first_sid = f"sess-judge-{calls[0]['tid']}"
        self.assertEqual(calls[1]["resumed"], first_sid, "вторая задача должна продолжить ту же сессию judge")
        state = D.load_state()
        self.assertIn("judge", state.get("role_sessions", {}))

    def test_role_scope_rotates_after_big_context(self):
        """Контекст прошлого запуска роли выше ROTATE_TOKENS → следующий запуск без --resume + напоминание."""
        self.set_fake_bin(FAKE_BIN_RECORD)
        os.environ["FAKE_CTX_TOKENS"] = str(D.ROTATE_TOKENS + 1)
        self.addCleanup(lambda: os.environ.pop("FAKE_CTX_TOKENS", None))
        T.create_ticket(self.tickets_dir, owner="judge", title="Проверка A")
        T.create_ticket(self.tickets_dir, owner="judge", title="Проверка B")

        D.tick()
        self.wait_running()
        D.tick()
        self.wait_running()

        calls = [json.loads(l) for l in (self.tickets_dir.parent / "calls.jsonl").read_text(encoding="utf-8")
                 .splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]["tid"], calls[1]["tid"], "вторая задача должна была дождаться своей очереди")
        self.assertIsNone(calls[1]["resumed"], "контекст первой сессии превысил порог — вторая начинает новую")
        self.assertIn("Начинаем новую сессию", calls[1]["prompt"])
        self.assertIn(".claude/roles/notes/judge.md", calls[1]["prompt"])

    def test_ceo_mention_writes_inbox_not_a_role_run(self):
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Для CEO",
                                now=dt("2026-09-27T12:00:00+04:00"))
        T.append_log(path, "researcher", "Нужно решение владельца. @ceo подскажи.",
                     now=dt("2026-09-27T12:01:00+04:00"))
        T.write_header_updates(path, {"status": "needs_owner"}, now=dt("2026-09-27T12:01:00+04:00"))
        D.tick(now=dt("2026-09-27T12:02:00+04:00"))
        self.assertTrue(D.CEO_INBOX.exists())
        inbox = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertIn("@ceo", inbox)
        self.assertIn("needs_owner", inbox)
        self.assertEqual(D.RUNNING, {})  # ceo не запускается диспетчером как роль


if __name__ == "__main__":
    unittest.main()
