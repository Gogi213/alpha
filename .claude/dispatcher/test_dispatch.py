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

    def test_backlog_is_fully_ignored_even_with_mention(self):
        """v1.1: backlog — перенос из TASKS.md, диспетчер её не трогает вообще ни по одному правилу."""
        text = ("---\nid: TK-9\nowner: researcher\nstatus: backlog\nupdated: 2026-09-27T11:00:00+04:00\n---\n\n"
                "## Лог\n\n### 2026-09-27T11:05:00+04:00 researcher\n@judge даже упоминание не должно будить.\n")
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
turns = int(os.environ.get("FAKE_NUM_TURNS", "1"))
print(json.dumps({"session_id": f"sess-{role}-{tid}", "total_cost_usd": 0.01, "num_turns": turns,
                   "usage": {"input_tokens": ctx}}))
"""

# Дописывает запись в «## Лог», но НЕ трогает status в шапке — воспроизводит роль, забывшую увести
# задачу с todo (защита v1.1: логировано, но status остался todo — тоже ошибка роли).
FAKE_BIN_STUCK_TODO = r"""
import json, os, re, sys
from pathlib import Path
TICKETS_DIR = Path(os.environ["FAKE_TICKETS_DIR"])
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
tid = re.search(r"tickets/([\w-]+)\.md", prompt).group(1)
role = next((c for c in ("researcher", "engineer", "judge") if prompt.startswith(f"Ты — {c} ")), "unknown")
path = TICKETS_DIR / f"{tid}.md"
text = path.read_text(encoding="utf-8")
if not text.endswith("\n"):
    text += "\n"
if "## Лог" not in text:
    text += "\n## Лог\n"
text += f"\n### 2099-01-01T00:00:00+04:00 {role}\nСделал шаг, но забыл поправить статус.\n"
path.write_text(text, encoding="utf-8")
print(json.dumps({"session_id": f"sess-{tid}-{role}", "total_cost_usd": 0.05, "usage": {"input_tokens": 5}}))
"""

# Как FAKE_BIN_OK, но с задержкой — чтобы поймать процесс «на лету» для теста recover_active_runs.
FAKE_BIN_SLOW_OK = r"""
import json, os, re, sys, time
from pathlib import Path
time.sleep(1.5)
TICKETS_DIR = Path(os.environ["FAKE_TICKETS_DIR"])
args = sys.argv[1:]
prompt = args[args.index("-p") + 1]
tid = re.search(r"tickets/([\w-]+)\.md", prompt).group(1)
role = next((c for c in ("researcher", "engineer", "judge") if prompt.startswith(f"Ты — {c} ")), "unknown")
path = TICKETS_DIR / f"{tid}.md"
text = path.read_text(encoding="utf-8")
text = re.sub(r"(?m)^status:.*$", "status: done", text, count=1)
if not text.endswith("\n"):
    text += "\n"
if "## Лог" not in text:
    text += "\n## Лог\n"
text += f"\n### 2099-01-01T00:00:00+04:00 {role}\nШаг (медленный). status: done.\n"
path.write_text(text, encoding="utf-8")
print(json.dumps({"session_id": f"sess-{tid}-{role}", "total_cost_usd": 0.01, "usage": {"input_tokens": 5}}))
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
                       "CEO_INBOX", "CEO_WAKE_LOG", "CLAUDE_BIN", "MAX_PARALLEL", "RUN_TIMEOUT")}
        D.TICKETS_DIR = self.tickets_dir
        D.PROJECT_ROOT = self.base
        D.STATE_FILE = self.dispatcher_dir / "state.json"
        D.RUNS_DIR = self.dispatcher_dir / "runs"
        D.RUNS_LOG = self.dispatcher_dir / "runs.log"
        D.CEO_INBOX = self.dispatcher_dir / "ceo-inbox.md"
        D.CEO_WAKE_LOG = self.dispatcher_dir / "ceo-wake.log"
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

    def test_multiturn_run_does_not_rotate_on_summed_usage(self):
        """CEO 27.09: usage — сумма по ходам запуска. Многоходовой прогон с большой суммой, но
        нормальным контекстом последнего хода, НЕ должен рвать долгую сессию (раньше рвал — баг)."""
        self.set_fake_bin(FAKE_BIN_RECORD)
        # сумма по 5 ходам (≈ 1,25×порога) за порогом, но на ход — четверть порога, сильно меньше
        per_turn = D.ROTATE_TOKENS // 4
        os.environ["FAKE_CTX_TOKENS"] = str(per_turn * 5)
        os.environ["FAKE_NUM_TURNS"] = "5"
        self.assertGreater(per_turn * 5, D.ROTATE_TOKENS, "сумма должна была бы превышать порог")
        self.assertLess(per_turn, D.ROTATE_TOKENS, "а контекст хода — нет")
        self.addCleanup(lambda: os.environ.pop("FAKE_CTX_TOKENS", None))
        self.addCleanup(lambda: os.environ.pop("FAKE_NUM_TURNS", None))
        T.create_ticket(self.tickets_dir, owner="judge", title="Проверка A")
        T.create_ticket(self.tickets_dir, owner="judge", title="Проверка B")

        D.tick()
        self.wait_running()
        D.tick()
        self.wait_running()

        calls = [json.loads(l) for l in (self.tickets_dir.parent / "calls.jsonl").read_text(encoding="utf-8")
                 .splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(calls[1]["resumed"], "контекст ХОДА не превышен — сессия должна продолжиться")
        self.assertNotIn("Начинаем новую сессию", calls[1]["prompt"])

    def test_stuck_todo_after_log_retries_then_blocks(self):
        """v1.1: лог есть, но status остался todo — тоже ошибка роли (повтор → blocked)."""
        self.set_fake_bin(FAKE_BIN_STUCK_TODO)
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Забывчивый")
        D.tick()
        self.wait_running()
        tkt = T.read_ticket(path)
        self.assertEqual(tkt.status, "blocked")
        # 2 записи роли (исходная + повтор) + 1 запись dispatcher про блокировку
        self.assertEqual(len(tkt.log), 3)
        inbox = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertIn("todo дважды подряд", inbox)

    def test_min_gap_prevents_immediate_relaunch_via_tick(self):
        """v1.1: MIN_GAP_S — троттлинг, не ошибка; ticket остаётся todo, просто не запускается сразу."""
        self.set_fake_bin(FAKE_BIN_SILENT)
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Слишком часто")
        state = D.load_state()
        D._record_launch(state, path.stem, datetime.now().astimezone())
        D.save_state(state)
        n = D.tick()
        self.assertEqual(n, 0, "MIN_GAP_S должен был не дать перезапуститься сразу")
        self.assertEqual(D.RUNNING, {})

    def test_daily_budget_blocks_new_launches(self):
        """v1.1: суточный потолок стоимости исчерпан — новые запуски не стартуют, строка в ceo-inbox."""
        self.set_fake_bin(FAKE_BIN_SILENT)
        state = D.load_state()
        D._add_cost(state, datetime.now().astimezone(), D.DAILY_COST_USD)
        D.save_state(state)
        T.create_ticket(self.tickets_dir, owner="researcher", title="Под потолком")
        n = D.tick()
        self.assertEqual(n, 0)
        self.assertEqual(D.RUNNING, {})
        inbox = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertIn("суточный потолок стоимости исчерпан", inbox)
        # дедуп: второй тик не должен добавить вторую такую же строку
        D.tick()
        self.assertEqual(D.CEO_INBOX.read_text(encoding="utf-8").count("суточный потолок стоимости исчерпан"), 1)

    def test_recover_active_runs_adopts_alive_process(self):
        """v1.1: перезапуск диспетчера во время прогона — живой pid подхватывается, не запускается повторно."""
        self.set_fake_bin(FAKE_BIN_SLOW_OK)
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Долгий")
        D.tick()
        self.assertIn(path.stem, D.RUNNING)
        pid = D.RUNNING[path.stem]["pid"]
        real_popen = D.RUNNING[path.stem]["popen"]
        # закрываем родительские файловые дескрипторы сразу (не через addCleanup — тот выполняется
        # ПОСЛЕ tearDown, а tearDown уже пытается удалить временный каталог на Windows)
        D.RUNNING[path.stem]["out_fh"].close()
        D.RUNNING[path.stem]["err_fh"].close()

        # "перезапуск диспетчера": теряем всё, что жило только в памяти процесса
        D.RUNNING.clear()
        state = D.load_state()
        self.assertIn(path.stem, state.get("active_runs", {}), "зеркало в state.json должно было остаться")

        D.recover_active_runs(state, datetime.now().astimezone())
        self.assertIn(path.stem, D.RUNNING, "живой pid должен быть подхвачен, не потерян")
        self.assertIsNone(D.RUNNING[path.stem]["popen"])
        self.assertEqual(D.RUNNING[path.stem]["pid"], pid)
        D.save_state(state)

        self.wait_running()  # доиграть до конца по pid (_pid_alive/_finish_run), без второго запуска
        real_popen.wait(timeout=5)  # реап собственного дочернего процесса (уже завершился)
        tkt = T.read_ticket(path)
        self.assertEqual(len(tkt.log), 1, "recover не должен был запустить процесс повторно")
        self.assertEqual(tkt.status, "done")

    def test_recover_active_runs_processes_finished_while_down(self):
        """v1.1: процесс успел закончиться, пока диспетчер не работал — recover доводит его до конца сам."""
        self.set_fake_bin(FAKE_BIN_OK)
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Быстрый")
        D.tick()
        self.assertIn(path.stem, D.RUNNING)
        real_popen = D.RUNNING[path.stem]["popen"]
        real_popen.wait(timeout=10)  # дождались настоящего завершения процесса
        D.RUNNING[path.stem]["out_fh"].close()
        D.RUNNING[path.stem]["err_fh"].close()
        D.RUNNING.clear()  # "перезапуск" — без вызова _poll_running/_finish_run

        state = D.load_state()
        self.assertIn(path.stem, state.get("active_runs", {}), "зеркало должно остаться, раз мы не поллили")
        D.recover_active_runs(state, datetime.now().astimezone())
        D.save_state(state)

        self.assertEqual(D.RUNNING, {}, "процесс уже мёртв — не должен попасть в RUNNING")
        self.assertNotIn(path.stem, D.load_state().get("active_runs", {}))
        tkt = T.read_ticket(path)
        self.assertEqual(len(tkt.log), 1)
        self.assertEqual(tkt.header.get("status"), "done")

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

    def test_ceo_wake_log_mirrors_inbox(self):
        """v1.1: каждая запись ceo-inbox.md дублируется короткой строкой в ceo-wake.log (Monitor CEO)."""
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Для CEO",
                                now=dt("2026-09-27T12:00:00+04:00"))
        T.append_log(path, "researcher", "Нужно решение владельца. @ceo подскажи.",
                     now=dt("2026-09-27T12:01:00+04:00"))
        T.write_header_updates(path, {"status": "needs_owner"}, now=dt("2026-09-27T12:01:00+04:00"))
        D.tick(now=dt("2026-09-27T12:02:00+04:00"))
        self.assertTrue(D.CEO_WAKE_LOG.exists())
        wake = D.CEO_WAKE_LOG.read_text(encoding="utf-8")
        self.assertIn(path.stem, wake)
        self.assertIn("needs_owner", wake)
        # столько же строк, сколько записей ушло в ceo-inbox.md за этот тик
        inbox_lines = [ln for ln in D.CEO_INBOX.read_text(encoding="utf-8").splitlines() if ln.strip()]
        wake_lines = [ln for ln in wake.splitlines() if ln.strip()]
        self.assertEqual(len(wake_lines), len(inbox_lines))


import tickets as TK  # noqa: E402  (CLI — new/comment/start/status)


class TicketsCliStartTests(unittest.TestCase):
    """v1.1: `tickets.py start` — backlog → todo, и только backlog."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tickets_dir = Path(self.tmp.name) / "tickets"
        self._orig = TK.TICKETS_DIR
        TK.TICKETS_DIR = self.tickets_dir

    def tearDown(self):
        TK.TICKETS_DIR = self._orig
        self.tmp.cleanup()

    def test_start_moves_backlog_to_todo(self):
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Из TASKS.md", status="backlog")
        rc = TK.main(["start", path.stem])
        self.assertEqual(rc, 0)
        self.assertEqual(T.read_ticket(path).status, "todo")

    def test_start_refuses_non_backlog(self):
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Уже todo", status="todo")
        rc = TK.main(["start", path.stem])
        self.assertEqual(rc, 1)
        self.assertEqual(T.read_ticket(path).status, "todo")

    def test_new_backlog_flag(self):
        rc = TK.main(["new", "--owner", "engineer", "--title", "Перенесено", "--backlog"])
        self.assertEqual(rc, 0)
        tickets = T.list_tickets(self.tickets_dir)
        self.assertEqual(len(tickets), 1)
        self.assertEqual(T.read_ticket(tickets[0]).status, "backlog")


class RateLimitAndBudgetTests(unittest.TestCase):
    """v1.1, чистые функции — без процессов и без сети."""

    def setUp(self):
        self.state = {}
        self.now = dt("2026-09-27T12:00:00+04:00")

    def test_min_gap_blocks_then_clears(self):
        D._record_launch(self.state, "TK-1", self.now)
        soon = self.now + timedelta(seconds=10)
        self.assertTrue(D._rate_limited(self.state, "TK-1", soon))
        later = self.now + timedelta(seconds=D.MIN_GAP_S + 1)
        self.assertFalse(D._rate_limited(self.state, "TK-1", later))

    def test_other_ticket_not_affected(self):
        D._record_launch(self.state, "TK-1", self.now)
        self.assertFalse(D._rate_limited(self.state, "TK-2", self.now + timedelta(seconds=1)))

    def test_max_runs_per_hour(self):
        step = timedelta(seconds=D.MIN_GAP_S + 1)
        for i in range(D.MAX_RUNS_PER_TICKET_HOUR):
            D._record_launch(self.state, "TK-1", self.now + i * step)
        probe = self.now + D.MAX_RUNS_PER_TICKET_HOUR * step
        self.assertTrue(D._rate_limited(self.state, "TK-1", probe), "часовой лимит должен был сработать")
        far_later = self.now + timedelta(hours=2)
        self.assertFalse(D._rate_limited(self.state, "TK-1", far_later), "час прошёл — лимит снят")

    def test_daily_budget_exceeded_and_notify_once(self):
        self.assertFalse(D._daily_budget_exceeded(self.state, self.now))
        D._add_cost(self.state, self.now, D.DAILY_COST_USD - 1)
        self.assertFalse(D._daily_budget_exceeded(self.state, self.now))
        D._add_cost(self.state, self.now, 1.5)
        self.assertTrue(D._daily_budget_exceeded(self.state, self.now))

    def test_daily_budget_is_per_day(self):
        D._add_cost(self.state, self.now, D.DAILY_COST_USD)
        tomorrow = self.now + timedelta(days=1)
        self.assertFalse(D._daily_budget_exceeded(self.state, tomorrow))


class ContextTokensTests(unittest.TestCase):
    """v1.1 (CEO 27.09): usage в JSON `claude -p` — сумма по всем ходам запуска, не контекст одного
    хода. Ротация должна смотреть на последний ход (usage.iterations[-1] или ctx_sum // num_turns),
    иначе долгий многоходовый запуск рвёт долгую сессию сразу же (живой прогон: ctx_sum=333886 при
    реальном контексте хода ~52 тыс.)."""

    def test_sum_is_plain_total_of_usage_dict(self):
        usage = {"input_tokens": 100, "cache_read_input_tokens": 200, "cache_creation_input_tokens": 50}
        self.assertEqual(D._context_tokens_sum(usage), 350)
        self.assertEqual(D._context_tokens_sum(None), 0)

    def test_last_uses_final_iteration_when_present(self):
        result = {
            "num_turns": 3,
            "usage": {
                "input_tokens": 300, "cache_read_input_tokens": 300_000, "cache_creation_input_tokens": 33_586,
                "iterations": [
                    {"input_tokens": 50, "cache_read_input_tokens": 10_000, "cache_creation_input_tokens": 20_000},
                    {"input_tokens": 60, "cache_read_input_tokens": 15_000, "cache_creation_input_tokens": 5_000},
                    {"input_tokens": 162, "cache_read_input_tokens": 50_000, "cache_creation_input_tokens": 2_000},
                ],
            },
        }
        # контекст последнего хода — только третья итерация, не сумма usage целиком (333 886)
        self.assertEqual(D._context_tokens_last(result), 162 + 50_000 + 2_000)
        self.assertEqual(D._context_tokens_sum(result["usage"]), 300 + 300_000 + 33_586)

    def test_last_falls_back_to_sum_over_num_turns_without_iterations(self):
        # живой смоук 27.09 (без iterations в реальном выводе на тот момент): ctx_sum=333886, ходов не 1
        result = {"num_turns": 5, "usage": {"input_tokens": 162, "cache_read_input_tokens": 300_000,
                                             "cache_creation_input_tokens": 33_724}}
        total = D._context_tokens_sum(result["usage"])
        self.assertEqual(D._context_tokens_last(result), total // 5)
        self.assertLess(D._context_tokens_last(result), total)  # не завышен суммой всех ходов

    def test_last_falls_back_to_sum_when_num_turns_missing_or_zero(self):
        result = {"usage": {"input_tokens": 100}}
        self.assertEqual(D._context_tokens_last(result), 100)
        result_zero = {"num_turns": 0, "usage": {"input_tokens": 100}}
        self.assertEqual(D._context_tokens_last(result_zero), 100)  # 0 ходов — не делить на ноль

    def test_last_handles_empty_result(self):
        self.assertEqual(D._context_tokens_last({}), 0)

    def test_single_turn_run_sum_equals_last(self):
        """Однократный запуск (как в большинстве фейковых тестов) — сумма и последний ход совпадают."""
        result = {"usage": {"input_tokens": 250_001}}
        self.assertEqual(D._context_tokens_last(result), D._context_tokens_sum(result["usage"]))


class DeckSshTests(unittest.TestCase):
    """v1.1: умолчания ssh на Steam Deck (кириллический HOME ломает ~/.ssh по умолчанию)."""

    def test_tilde_path_not_quoted_away(self):
        """Живой прогон 27.09 поймал: shlex.quote('~/x') = "'~/x'" — remote-шелл её не раскрывает."""
        self.assertEqual(D._remote_test_arg("~/alpha/queue/STATUS"), "~/alpha/queue/STATUS")
        self.assertEqual(D._remote_test_arg("~"), "~")

    def test_tilde_path_rest_still_escaped(self):
        import shlex
        raw = "~/alpha/queue/a b;rm -rf /"
        arg = D._remote_test_arg(raw)
        self.assertEqual(arg, "~" + shlex.quote(raw[1:]))
        self.assertTrue(arg.startswith("~'") or arg.startswith("~/"))  # тильда сама не в кавычках

    def test_absolute_path_quoted_as_before(self):
        self.assertIn("'", D._remote_test_arg("/tmp/a b"))

    def test_command_uses_expected_key_host_and_options(self):
        captured = {}

        class FakeResult:
            returncode = 0

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeResult()

        for var in ("ALPHA_DECK_KEY", "ALPHA_DECK_HOST", "ALPHA_DECK_KNOWN_HOSTS"):
            os.environ.pop(var, None)
        orig_run = D.subprocess.run
        D.subprocess.run = fake_run
        try:
            ok = D._deck_file_exists("~/alpha/queue/STATUS")
        finally:
            D.subprocess.run = orig_run

        self.assertTrue(ok)
        cmd = captured["cmd"]
        self.assertEqual(cmd[0], "ssh")
        self.assertEqual(cmd[cmd.index("-i") + 1], r"C:/Users/Георгий/.ssh/id_rsa")
        self.assertIn("UserKnownHostsFile=C:/Users/Георгий/.ssh/known_hosts", cmd)
        self.assertIn("BatchMode=yes", cmd)
        self.assertIn("ConnectTimeout=8", cmd)
        self.assertEqual(cmd[-2], "deck@192.168.1.49")
        self.assertTrue(cmd[-1].startswith("test -e "))
        self.assertIn("~/alpha/queue/STATUS", cmd[-1])


if __name__ == "__main__":
    unittest.main()
