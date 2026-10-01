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

    def test_log_raw_captures_whole_section_including_headerless_lines(self):
        """CEO 27.09, TK-005: роли пишут без заголовка `###` — log_raw должен содержать их текст,
        даже если `_parse_log` (по заголовкам) их не разобрал как LogEntry."""
        text = ("---\nid: TK-005\nowner: engineer\nstatus: in_progress\nupdated: 2026-09-27T23:00:00+04:00\n"
                "---\n\n## Лог\n\n- 27.09 ~23:50 (инженер, запуск 1) сделал шаг, дальше доделать\n")
        tkt = T.parse_text(text, Path("TK-005.md"))
        self.assertEqual(tkt.log, [])  # ни одной по-настоящему разобранной записи — заголовка нет
        self.assertIn("сделал шаг", tkt.log_raw)


class MentionsSinceTests(unittest.TestCase):
    """CEO 27.09, TK-005: упоминания и рост секции — по сырому тексту, не по заголовкам `###`."""

    def test_headerless_bullet_mention_is_found(self):
        log_raw = "\n- 27.09 ~23:50 (инженер, запуск 1) сделал шаг, дальше @judge глянь\n"
        self.assertEqual(T.mentions_since(log_raw, 0), {"judge"})

    def test_researcher_bracket_style_mention_is_found(self):
        log_raw = "\n- 27.09 23:25 [researcher] нашёл эффект, @ceo интересно посмотреть\n"
        self.assertEqual(T.mentions_since(log_raw, 0), {"ceo"})

    def test_self_mention_excluded_when_author_known_from_preceding_header(self):
        log_raw = ("\n### 2026-09-27T21:00:00+04:00 engineer\nзапуск 1\n"
                    "- 27.09 ~23:50 (инженер) заметка себе на будущее @engineer\n")
        self.assertEqual(T.mentions_since(log_raw, 0), set())

    def test_self_mention_not_excluded_without_any_preceding_header(self):
        """Автор неизвестен (нет заголовка вообще) — не исключаем: пропуск дороже лишнего повтора."""
        log_raw = "\n- 27.09 ~23:50 (инженер) заметка себе @engineer, но без заголовка выше\n"
        self.assertEqual(T.mentions_since(log_raw, 0), {"engineer"})

    def test_nothing_new_since_seen_len_is_silent(self):
        log_raw = "\n- 27.09 ~23:50 @judge глянь\n"
        self.assertEqual(T.mentions_since(log_raw, len(log_raw)), set())

    def test_only_tail_after_seen_len_is_scanned(self):
        head = "\n- старая запись без упоминаний\n"
        tail = "- новая запись @judge посмотри\n"
        log_raw = head + tail
        self.assertEqual(T.mentions_since(log_raw, len(head)), {"judge"})
        self.assertEqual(T.mentions_since(log_raw, 0), {"judge"})  # то же упоминание видно и от начала


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
        # status: waiting (без выполненного wait_for) — нейтральный статус без своего правила,
        # чтобы изолированно проверить именно дедуп упоминания, а не правило (а') in_progress-resume
        text = ("---\nid: TK-2\nowner: researcher\nstatus: waiting\nupdated: 2026-09-27T11:00:00+04:00\n---\n\n"
                "## Лог\n\n### 2026-09-27T11:05:00+04:00 researcher\n@judge глянь план.\n")
        tkt = self.ticket_from(text)
        # v1.5: дедуп упоминания — по длине секции на момент запуска (log_len_at_launch), не по времени
        self.state.setdefault("sessions", {})["TK-2::judge"] = {"log_len_at_launch": len(tkt.log_raw)}
        dec = D.decide(tkt, self.state, self.now)
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

    def test_in_progress_without_mention_wakes_owner_to_resume(self):
        """v1.1 (судья 27.09, п.2 «обязательно»): без этого многошаговый тикет замирал после первой
        сессии — устав ролей обещает продолжение другой сессией, диспетчер никого не будил."""
        text = ("---\nid: TK-8\nowner: researcher\nstatus: in_progress\nupdated: 2026-09-27T11:00:00+04:00\n---\n\n"
                "## Лог\n\n### 2026-09-27T11:05:00+04:00 researcher\nРаботаю дальше.\n")
        dec = D.decide(self.ticket_from(text), self.state, self.now)
        self.assertEqual((dec.role, dec.reason), ("researcher", "in_progress-resume"))

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
                       "CEO_INBOX", "CEO_WAKE_LOG", "CLAUDE_BIN", "MAX_PARALLEL", "RUN_TIMEOUT",
                       "PID_EXPECT_NAME")}
        D.TICKETS_DIR = self.tickets_dir
        D.PROJECT_ROOT = self.base
        D.STATE_FILE = self.dispatcher_dir / "state.json"
        D.RUNS_DIR = self.dispatcher_dir / "runs"
        D.RUNS_LOG = self.dispatcher_dir / "runs.log"
        D.CEO_INBOX = self.dispatcher_dir / "ceo-inbox.md"
        D.CEO_WAKE_LOG = self.dispatcher_dir / "ceo-wake.log"
        # фейковый "claude" в тестах — это sys.executable (python.exe/python3), не claude.exe
        D.PID_EXPECT_NAME = Path(sys.executable).stem
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

    def test_launch_run_strips_host_session_env(self):
        """(4) обязательно: без этого дочерний claude наследует CLAUDE_CODE_HOST_SESSION_ID сессии CEO —
        role_context.py/role_memory.py принимают роль за CEO (судья 27.09, пилот TK-001)."""
        os.environ["CLAUDE_CODE_HOST_SESSION_ID"] = "local_ceo-host-id-fake"
        self.addCleanup(lambda: os.environ.pop("CLAUDE_CODE_HOST_SESSION_ID", None))
        self.set_fake_bin(FAKE_BIN_SILENT)
        captured_env = {}
        orig_popen = D._popen

        def spy_popen(cmd, **kwargs):
            captured_env.update(kwargs.get("env") or {})
            return orig_popen(cmd, **kwargs)

        D._popen = spy_popen
        try:
            path = T.create_ticket(self.tickets_dir, owner="researcher", title="Утечка env")
            D.tick()
        finally:
            D._popen = orig_popen
        self.assertNotIn("CLAUDE_CODE_HOST_SESSION_ID", captured_env)
        self.assertEqual(captured_env.get("ALPHA_ROLE"), "researcher")
        for info in list(D.RUNNING.values()):
            info["popen"].wait(timeout=10)
            for fh in (info.get("out_fh"), info.get("err_fh")):
                if fh:
                    fh.close()
        D.RUNNING.clear()

    def test_launch_run_sets_model_effort_and_budget_cap(self):
        """v1.3 (владелец 27.09): модель/усилие ВСЕГДА явно — пилот на умолчаниях CLI стоил $6,8."""
        self.set_fake_bin(FAKE_BIN_SILENT)
        captured_cmd = []
        orig_popen = D._popen

        def spy_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return orig_popen(cmd, **kwargs)

        D._popen = spy_popen
        try:
            path = T.create_ticket(self.tickets_dir, owner="judge", title="Проверка флагов")
            D.tick()
        finally:
            D._popen = orig_popen
        # v1.6.1: модель — по роли (ROLE_MODEL[judge], по умолчанию opus), не общий CLAUDE_MODEL
        self.assertEqual(captured_cmd[captured_cmd.index("--model") + 1], D.ROLE_MODEL["judge"])
        self.assertEqual(captured_cmd[captured_cmd.index("--effort") + 1], "xhigh")  # ROLE_EFFORT[judge]
        cap = float(captured_cmd[captured_cmd.index("--max-budget-usd") + 1])
        self.assertAlmostEqual(cap, min(D.RUN_CAP_USD, D.DEFAULT_TICKET_BUDGET_USD))
        for info in list(D.RUNNING.values()):
            info["popen"].wait(timeout=10)
            for fh in (info.get("out_fh"), info.get("err_fh")):
                if fh:
                    fh.close()
        D.RUNNING.clear()

    def test_launch_run_model_follows_role_not_global(self):
        """v1.6.1: --model запуска — ROLE_MODEL[роль]; инженер не получает модель Судьи и наоборот."""
        self.set_fake_bin(FAKE_BIN_SILENT)
        captured_cmd = []
        orig_popen = D._popen
        orig_role_model = D.ROLE_MODEL

        def spy_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return orig_popen(cmd, **kwargs)

        D._popen = spy_popen
        D.ROLE_MODEL = {"judge": "claude-opus-5-5", "engineer": "claude-sonnet-5-5-test",
                        "researcher": "claude-sonnet-5-5-test"}
        try:
            T.create_ticket(self.tickets_dir, owner="engineer", title="Модель инженера")
            D.tick()
        finally:
            D._popen = orig_popen
            D.ROLE_MODEL = orig_role_model
        self.assertEqual(captured_cmd[captured_cmd.index("--model") + 1], "claude-sonnet-5-5-test")
        for info in list(D.RUNNING.values()):
            info["popen"].wait(timeout=10)
            for fh in (info.get("out_fh"), info.get("err_fh")):
                if fh:
                    fh.close()
        D.RUNNING.clear()

    def test_launch_run_uses_haiku_model_for_allowed_executor(self):
        """v1.4, судья TK-002 п.5: executor: haiku подменяет --model, остальное (эффорт, лимиты) как обычно."""
        self.set_fake_bin(FAKE_BIN_SILENT)
        path = T.create_ticket(self.tickets_dir, owner="engineer", title="Механическая")  # reviewer=None по умолчанию
        T.write_header_updates(path, {"executor": "haiku", "kind": "file-move"})
        captured_cmd = []
        orig_popen = D._popen

        def spy_popen(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return orig_popen(cmd, **kwargs)

        D._popen = spy_popen
        try:
            D.tick()
        finally:
            D._popen = orig_popen
        self.assertEqual(captured_cmd[captured_cmd.index("--model") + 1], D.CLAUDE_HAIKU_MODEL)
        for info in list(D.RUNNING.values()):
            info["popen"].wait(timeout=10)
            for fh in (info.get("out_fh"), info.get("err_fh")):
                if fh:
                    fh.close()
        D.RUNNING.clear()

    def test_tick_blocks_ticket_with_forbidden_haiku_combo(self):
        """Вторая защита в диспетчере (условие г) — на случай ручной правки шапки в обход tickets.py."""
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Обход")
        T.write_header_updates(path, {"executor": "haiku", "kind": "file-move"})
        n = D.tick()
        self.assertEqual(n, 0)
        tkt = T.read_ticket(path)
        self.assertEqual(tkt.status, "blocked")
        self.assertIn("researcher", tkt.log[-1].text)
        self.assertIn("researcher", D.CEO_INBOX.read_text(encoding="utf-8"))

    def test_finish_run_flags_haiku_ticket_actually_run_on_other_model(self):
        path = T.create_ticket(self.tickets_dir, owner="engineer", title="Подмена модели")
        T.write_header_updates(path, {"executor": "haiku", "kind": "file-move"})
        tid = path.stem
        state = D.load_state()
        run_file = self.dispatcher_dir / "wrongmodel.json"
        run_file.write_text(json.dumps({"session_id": "s-haiku-1", "total_cost_usd": 0.01,
                                         "modelUsage": {"claude-opus-5-5": {"costUSD": 0.01}}}),
                             encoding="utf-8")
        info = {"role": "engineer", "popen": None, "pid": None, "started": dt("2026-09-27T12:00:00+04:00"),
                "attempt": 0, "run_file": run_file, "err_file": run_file, "out_fh": None, "err_fh": None,
                "reason": "todo", "run_cap_usd": 1.0, "status_at_launch": "todo", "executor": "haiku"}
        D._finish_run(tid, info, state, dt("2026-09-27T12:01:00+04:00"), timed_out=False)
        self.assertIn("opus", D.CEO_INBOX.read_text(encoding="utf-8"))

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

    def test_hour_budget_blocks_new_launches(self):
        """v1.3, п.4: часовая скорость трат по всем ролям исчерпана — пауза + строка CEO."""
        self.set_fake_bin(FAKE_BIN_SILENT)
        state = D.load_state()
        D._record_cost_event(state, datetime.now().astimezone(), D.HOUR_COST_USD)
        D.save_state(state)
        T.create_ticket(self.tickets_dir, owner="researcher", title="Быстро потратили")
        n = D.tick()
        self.assertEqual(n, 0)
        self.assertEqual(D.RUNNING, {})
        self.assertIn("скорость трат", D.CEO_INBOX.read_text(encoding="utf-8"))

    def test_ticket_budget_blocks_new_launches_and_sets_needs_owner(self):
        """v1.3, п.2: бюджет ИМЕННО этой задачи исчерпан — needs_owner, другие тикеты не задеты."""
        self.set_fake_bin(FAKE_BIN_SILENT)
        expensive = T.create_ticket(self.tickets_dir, owner="researcher", title="Дорогая")
        cheap = T.create_ticket(self.tickets_dir, owner="engineer", title="Обычная")
        state = D.load_state()
        D.set_ticket_budget(state, expensive.stem, 5.0)
        D.add_ticket_cost(state, expensive.stem, 5.5)
        D.save_state(state)
        D.MAX_PARALLEL = 2
        n = D.tick()
        self.assertEqual(T.read_ticket(expensive).status, "needs_owner")
        self.assertIn(expensive.stem, D.CEO_INBOX.read_text(encoding="utf-8"))
        self.assertEqual(n, 1)  # дешёвая задача не задета чужим бюджетом
        self.assertIn(cheap.stem, D.RUNNING)
        for info in list(D.RUNNING.values()):
            info["popen"].wait(timeout=10)
            for fh in (info.get("out_fh"), info.get("err_fh")):
                if fh:
                    fh.close()
        D.RUNNING.clear()

    def test_money_no_json_is_undercount_not_run_cap(self):
        """v1.4 (судья TK-002 п.1д): запуск без JSON (убит) не досчитывается потолком запуска — иначе
        двойной счёт, если та же сессия потом продолжится (разница на resume уже подберёт реальное).
        Принимаем недоучёт на этот раз, не гадаем числом (было — списывали run_cap_usd, v1.3)."""
        state = D.load_state()
        run_file = self.dispatcher_dir / "nocost.json"
        run_file.write_text("", encoding="utf-8")
        info = {"role": "engineer", "popen": None, "pid": None, "started": dt("2026-09-27T12:00:00+04:00"),
                "attempt": 0, "run_file": run_file, "err_file": run_file, "out_fh": None, "err_fh": None,
                "reason": "todo", "run_cap_usd": 4.25, "status_at_launch": "todo"}
        T.create_ticket(self.tickets_dir, owner="engineer", title="Без JSON")
        tid = T.list_tickets(self.tickets_dir)[0].stem
        info_by_tid = dict(info)
        D._finish_run(tid, info_by_tid, state, dt("2026-09-27T12:05:00+04:00"), timed_out=True)
        self.assertAlmostEqual(D.ticket_cost_spent(state, tid), 0.0)
        self.assertAlmostEqual(state.get("daily_cost", {}).get("2026-09-27", 0.0), 0.0)

    def test_money_idle_run_over_half_budget_blocks_immediately(self):
        """п.5, холостой ход: дороже половины бюджета и ни записи, ни смены статуса — сразу blocked,
        без обычного одного повтора."""
        path = T.create_ticket(self.tickets_dir, owner="engineer", title="Холостой", now=dt("2026-09-27T12:00:00+04:00"))
        tid = path.stem
        state = D.load_state()
        D.set_ticket_budget(state, tid, 10.0)
        run_file = self.dispatcher_dir / "idle.json"
        run_file.write_text(json.dumps({"session_id": "s1", "total_cost_usd": 6.0}), encoding="utf-8")
        info = {"role": "engineer", "popen": None, "pid": None, "started": dt("2026-09-27T12:00:00+04:00"),
                "attempt": 0, "run_file": run_file, "err_file": run_file, "out_fh": None, "err_fh": None,
                "reason": "todo", "run_cap_usd": 6.0, "status_at_launch": "todo"}
        D._finish_run(tid, info, state, dt("2026-09-27T12:01:00+04:00"), timed_out=False)
        tkt = T.read_ticket(path)
        self.assertEqual(tkt.status, "blocked")
        self.assertIn("холостой ход", tkt.log[-1].text)
        inbox = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertIn("холостой ход", inbox)

    def test_money_idle_run_does_not_fire_when_status_changed(self):
        """Дорогой запуск, но статус изменился (роль что-то сделала) — не холостой ход."""
        path = T.create_ticket(self.tickets_dir, owner="engineer", title="Не холостой",
                                now=dt("2026-09-27T12:00:00+04:00"))
        tid = path.stem
        T.write_header_updates(path, {"status": "waiting", "wait_for": "file:/nope"},
                                now=dt("2026-09-27T12:00:30+04:00"))
        state = D.load_state()
        D.set_ticket_budget(state, tid, 10.0)
        run_file = self.dispatcher_dir / "notidle.json"
        run_file.write_text(json.dumps({"session_id": "s1", "total_cost_usd": 6.0}), encoding="utf-8")
        info = {"role": "engineer", "popen": None, "pid": None, "started": dt("2026-09-27T12:00:00+04:00"),
                "attempt": 0, "run_file": run_file, "err_file": run_file, "out_fh": None, "err_fh": None,
                "reason": "todo", "run_cap_usd": 6.0, "status_at_launch": "todo"}
        D._finish_run(tid, info, state, dt("2026-09-27T12:01:00+04:00"), timed_out=False)
        self.assertEqual(T.read_ticket(path).status, "waiting")  # не blocked — статус роль таки сменила

    def test_money_model_usage_warning_reaches_ceo_inbox(self):
        path = T.create_ticket(self.tickets_dir, owner="engineer", title="Не opus",
                                now=dt("2026-09-27T12:00:00+04:00"))
        tid = path.stem
        T.append_log(path, "engineer", "готово", now=dt("2026-09-27T12:00:30+04:00"))
        state = D.load_state()
        run_file = self.dispatcher_dir / "badmodel.json"
        run_file.write_text(json.dumps({"session_id": "s1", "total_cost_usd": 0.1,
                                         "modelUsage": {"fable-5-1": {"cost": 0.1}}}), encoding="utf-8")
        info = {"role": "engineer", "popen": None, "pid": None, "started": dt("2026-09-27T12:00:00+04:00"),
                "attempt": 0, "run_file": run_file, "err_file": run_file, "out_fh": None, "err_fh": None,
                "reason": "todo", "run_cap_usd": 8.0, "status_at_launch": "todo"}
        D._finish_run(tid, info, state, dt("2026-09-27T12:01:00+04:00"), timed_out=False)
        self.assertIn("fable-5-1", D.CEO_INBOX.read_text(encoding="utf-8"))

    def test_finish_run_expects_model_family_of_the_runs_role(self):
        """v1.6.1: Судья на opus — тишина в ceo-inbox; тот же opus у инженера (ждём sonnet) — тревога."""
        orig_role_model = D.ROLE_MODEL
        D.ROLE_MODEL = {"judge": "claude-opus-5-5", "engineer": "claude-sonnet-5-5",
                        "researcher": "claude-sonnet-5-5"}
        try:
            for role, sid, warns in (("judge", "s-j", False), ("engineer", "s-e", True)):
                path = T.create_ticket(self.tickets_dir, owner=role, title=f"Модель {role}",
                                        now=dt("2026-09-27T12:00:00+04:00"))
                tid = path.stem
                T.append_log(path, role, "готово", now=dt("2026-09-27T12:00:30+04:00"))
                state = D.load_state()
                run_file = self.dispatcher_dir / f"model-{role}.json"
                run_file.write_text(json.dumps({"session_id": sid, "total_cost_usd": 0.1,
                                                 "modelUsage": {"claude-opus-5-5": {"costUSD": 0.1}}}),
                                     encoding="utf-8")
                info = {"role": role, "popen": None, "pid": None, "started": dt("2026-09-27T12:00:00+04:00"),
                        "attempt": 0, "run_file": run_file, "err_file": run_file, "out_fh": None, "err_fh": None,
                        "reason": "todo", "run_cap_usd": 8.0, "status_at_launch": "todo"}
                D._finish_run(tid, info, state, dt("2026-09-27T12:01:00+04:00"), timed_out=False)
                inbox = D.CEO_INBOX.read_text(encoding="utf-8") if D.CEO_INBOX.exists() else ""
                self.assertEqual("modelUsage" in inbox, warns, f"{role}: {inbox!r}")
        finally:
            D.ROLE_MODEL = orig_role_model

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

    def test_sim5_parse_error_flood_is_deduped(self):
        """(5) обязательно: сломанный тикет — одна строка в ceo-inbox, не строка на каждый тик."""
        (self.tickets_dir / "X5.md").write_text("нет шапки тут\n", encoding="utf-8")
        for i in range(3):
            D.tick(now=dt("2026-09-27T12:00:00+04:00") + timedelta(seconds=15 * i))
        inbox = D.CEO_INBOX.read_text(encoding="utf-8") if D.CEO_INBOX.exists() else ""
        self.assertEqual(inbox.count("parse-error"), 1, "3 тика с одной и той же ошибкой — одна строка")

    def test_notify_parse_error_renotifies_on_different_text(self):
        """Дедуп ключом (тикет, ТЕКСТ ошибки) — сменился текст ошибки, значит сменилась причина."""
        state = {}
        D.notify_parse_error("X5", "ValueError: тикет без шапки", state, dt("2026-09-27T12:00:00+04:00"))
        D.notify_parse_error("X5", "ValueError: тикет без шапки", state, dt("2026-09-27T12:00:15+04:00"))
        D.notify_parse_error("X5", "UnicodeDecodeError: 'utf-8' codec can't decode byte", state,
                              dt("2026-09-27T12:00:30+04:00"))
        self.assertEqual(D.CEO_INBOX.read_text(encoding="utf-8").count("parse-error"), 2)

    def test_sim4_done_without_reviewer_notifies_ceo(self):
        """(4) обязательно: done без reviewer раньше никого не уведомлял — числа минуют Судью молча."""
        path = T.create_ticket(self.tickets_dir, owner="researcher", title="Без ревью",
                                now=dt("2026-09-27T12:00:00+04:00"))
        T.append_log(path, "researcher", "готово, числа: KPI 0,097", now=dt("2026-09-27T12:01:00+04:00"))
        T.write_header_updates(path, {"status": "done"}, now=dt("2026-09-27T12:01:00+04:00"))
        D.tick(now=dt("2026-09-27T12:02:00+04:00"))
        self.assertTrue(D.CEO_INBOX.exists())
        inbox = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertIn("no-reviewer", inbox)
        self.assertEqual(D.RUNNING, {})  # без reviewer некого запускать — но CEO уведомлён
        # дедуп: второй тик с тем же updated не добавляет вторую строку
        D.tick(now=dt("2026-09-27T12:02:15+04:00"))
        self.assertEqual(D.CEO_INBOX.read_text(encoding="utf-8").count("no-reviewer"), 1)

    def test_sim6_timeout_with_logged_progress_is_not_a_failure(self):
        """(6) обязательно: RUN_TIMEOUT назван в промпте, а прогресс до таймаута — не провал."""
        self.assertIn(str(int(D.RUN_TIMEOUT // 60)), D.build_prompt("engineer", "X6"))
        path = T.create_ticket(self.tickets_dir, owner="engineer", title="Долгий шаг",
                                now=dt("2026-09-27T12:00:00+04:00"))
        T.write_header_updates(path, {"status": "in_progress"}, now=dt("2026-09-27T12:00:00+04:00"))
        started = dt("2026-09-27T12:00:00+04:00")
        T.append_log(path, "engineer", "сделал шаг 1 (артефакт a.csv), дальше шаг 2",
                     now=started + timedelta(minutes=20))

        class FakeTimedOutPopen:
            pid = 424242

            def poll(self):
                return None  # «висит» до самого таймаута

            def kill(self):
                pass

            def wait(self, timeout=None):
                pass

        run_file = self.dispatcher_dir / "x6.json"
        run_file.write_text("", encoding="utf-8")  # процесс убит — JSON не дописан
        D.RUNNING["X6"] = {"role": "engineer", "popen": FakeTimedOutPopen(), "pid": 424242, "started": started,
                            "attempt": 0, "run_file": run_file, "err_file": run_file, "out_fh": None,
                            "err_fh": None, "reason": "todo"}
        state = D.load_state()
        D._poll_running(state, started + timedelta(minutes=D.RUN_TIMEOUT // 60 + 1))
        D.save_state(state)
        self.assertEqual(D.RUNNING, {}, "прогресс есть — не должно быть повтора")
        self.assertEqual(T.read_ticket(path).status, "in_progress")  # роль сама решит дальше, не blocked

    def test_tk005_headerless_engineer_bullet_is_not_a_false_blocked(self):
        """Воспроизводит боевой TK-005 (CEO 27.09): Инженер дважды дописал «- 27.09 ~23:50 (инженер,
        запуск 1) …» без заголовка `###`; диспетчер считал это отсутствием записи → повтор → blocked."""
        path = T.create_ticket(self.tickets_dir, owner="engineer", title="TK-005",
                                now=dt("2026-09-27T23:00:00+04:00"))
        T.write_header_updates(path, {"status": "in_progress"}, now=dt("2026-09-27T23:00:00+04:00"))
        log_len_at_launch = len(T.read_ticket(path).log_raw)
        started = dt("2026-09-27T23:00:00+04:00")

        # роль дописывает БЕЗ заголовка ### — ровно формат из боевого лога
        content = path.read_text(encoding="utf-8")
        content += "\n- 27.09 ~23:50 (инженер, запуск 1) сделал шаг, дальше доделать\n"
        path.write_text(content, encoding="utf-8")

        run_file = self.dispatcher_dir / "tk005.json"
        run_file.write_text(json.dumps({"session_id": "s-tk005", "total_cost_usd": 0.2}), encoding="utf-8")
        info = {"role": "engineer", "popen": None, "pid": None, "started": started, "attempt": 0,
                "run_file": run_file, "err_file": run_file, "out_fh": None, "err_fh": None, "reason": "todo",
                "run_cap_usd": 1.0, "status_at_launch": "in_progress", "log_len_at_launch": log_len_at_launch}
        state = D.load_state()
        D._finish_run(path.stem, info, state, started + timedelta(minutes=5), timed_out=False)
        self.assertEqual(T.read_ticket(path).status, "in_progress", "не должно было уйти в blocked")

    def test_tk005_second_headerless_bullet_also_counts(self):
        """Второй безголовый допис (как в бою — b0b63cb, затем e657090) тоже должен засчитаться."""
        path = T.create_ticket(self.tickets_dir, owner="engineer", title="TK-005b",
                                now=dt("2026-09-27T23:00:00+04:00"))
        T.write_header_updates(path, {"status": "in_progress"}, now=dt("2026-09-27T23:00:00+04:00"))
        content = path.read_text(encoding="utf-8")
        content += "\n- 27.09 ~23:50 (инженер, запуск 1) сделал шаг 1\n"
        path.write_text(content, encoding="utf-8")
        log_len_at_launch2 = len(T.read_ticket(path).log_raw)  # снимок на старте ВТОРОГО запуска

        content2 = path.read_text(encoding="utf-8")
        content2 += "- 27.09 ~00:05 (инженер, запуск 2) доделал, status: in_progress\n"
        path.write_text(content2, encoding="utf-8")

        run_file = self.dispatcher_dir / "tk005b.json"
        run_file.write_text(json.dumps({"session_id": "s-tk005b", "total_cost_usd": 0.2}), encoding="utf-8")
        info = {"role": "engineer", "popen": None, "pid": None, "started": dt("2026-09-27T23:50:00+04:00"),
                "attempt": 0, "run_file": run_file, "err_file": run_file, "out_fh": None, "err_fh": None,
                "reason": "in_progress-resume", "run_cap_usd": 1.0, "status_at_launch": "in_progress",
                "log_len_at_launch": log_len_at_launch2}
        state = D.load_state()
        D._finish_run(path.stem, info, state, dt("2026-09-28T00:10:00+04:00"), timed_out=False)
        self.assertEqual(T.read_ticket(path).status, "in_progress")

    def test_prompt_tells_role_to_use_tickets_comment(self):
        prompt = D.build_prompt("engineer", "TK-005")
        self.assertIn("tickets.py comment TK-005 --author engineer", prompt)


import tickets as TK  # noqa: E402  (CLI — new/comment/start/status)


class TicketsCliStartTests(unittest.TestCase):
    """v1.1: `tickets.py start` — backlog → todo, и только backlog.
    v1.3: `cmd_new`/`cmd_status` теперь всегда трогают state.json (бюджет задачи) — ОБЯЗАТЕЛЬНО
    песочница и на TK.TICKETS_DIR, и на D.STATE_FILE, иначе тест пишет в боевой state.json (было
    поймано на живом файле 27.09 — TK-001/TK-002 утекли в ticket_budget)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tickets_dir = Path(self.tmp.name) / "tickets"
        self._orig_tickets_dir = TK.TICKETS_DIR
        self._orig_state_file = D.STATE_FILE
        TK.TICKETS_DIR = self.tickets_dir
        D.STATE_FILE = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        TK.TICKETS_DIR = self._orig_tickets_dir
        D.STATE_FILE = self._orig_state_file
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

    def test_new_defaults_reviewer_judge_for_researcher_and_engineer(self):
        """(6) обязательно: без reviewer по умолчанию done молча минует проверку Судьи."""
        TK.main(["new", "--owner", "researcher", "--title", "А"])
        TK.main(["new", "--owner", "engineer", "--title", "Б"])
        tickets = {t.header["owner"]: t for t in (T.read_ticket(p) for p in T.list_tickets(self.tickets_dir))}
        self.assertEqual(tickets["researcher"].reviewer, "judge")
        self.assertEqual(tickets["engineer"].reviewer, "judge")

    def test_new_no_reviewer_flag_opts_out(self):
        TK.main(["new", "--owner", "researcher", "--title", "Без ревью", "--no-reviewer"])
        tkt = T.read_ticket(T.list_tickets(self.tickets_dir)[0])
        self.assertEqual(tkt.reviewer, "")

    def test_new_explicit_reviewer_overrides_default(self):
        TK.main(["new", "--owner", "researcher", "--title", "Себе на проверку", "--reviewer", "engineer"])
        tkt = T.read_ticket(T.list_tickets(self.tickets_dir)[0])
        self.assertEqual(tkt.reviewer, "engineer")

    def test_new_judge_owner_has_no_default_reviewer(self):
        TK.main(["new", "--owner", "judge", "--title", "Судейское"])
        tkt = T.read_ticket(T.list_tickets(self.tickets_dir)[0])
        self.assertEqual(tkt.reviewer, "")

    def test_new_budget_flag_writes_state_not_header(self):
        """v1.3: --budget пишет в state.json, НЕ в шапку тикета (роль бюджет не видит)."""
        TK.main(["new", "--owner", "engineer", "--title", "Дорогая", "--budget", "L"])
        path = T.list_tickets(self.tickets_dir)[0]
        self.assertNotIn("budget_usd", T.read_ticket(path).header)
        state = D.load_state()
        self.assertEqual(D.ticket_budget_usd(state, path.stem), 25.0)

    def test_new_default_budget_is_m(self):
        TK.main(["new", "--owner", "researcher", "--title", "Обычная"])
        path = T.list_tickets(self.tickets_dir)[0]
        state = D.load_state()
        self.assertEqual(D.ticket_budget_usd(state, path.stem), D.BUDGET_PRESETS["M"])

    def test_new_budget_rejects_garbage(self):
        rc = TK.main(["new", "--owner", "researcher", "--title", "Плохой бюджет", "--budget", "много"])
        self.assertEqual(rc, 1)

    def test_status_shows_spent_column_for_ceo(self):
        TK.main(["new", "--owner", "engineer", "--title", "С тратами", "--budget", "S"])
        path = T.list_tickets(self.tickets_dir)[0]
        state = D.load_state()
        D.add_ticket_cost(state, path.stem, 1.5)
        D.save_state(state)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            TK.main(["status"])
        out = buf.getvalue()
        self.assertIn("потрачено", out)
        self.assertIn("$1.50/$3.00", out)

    def test_new_haiku_requires_kind(self):
        rc = TK.main(["new", "--owner", "engineer", "--title", "Без kind", "--executor", "haiku"])
        self.assertEqual(rc, 1)
        self.assertEqual(T.list_tickets(self.tickets_dir), [])

    def test_new_haiku_default_reviewer_judge_is_refused(self):
        """researcher/engineer получают reviewer: judge по умолчанию — с executor: haiku это запрещённая
        комбинация (условие г), даже если пользователь не просил reviewer явно."""
        rc = TK.main(["new", "--owner", "engineer", "--title", "Забыли --no-reviewer",
                      "--executor", "haiku", "--kind", "file-move"])
        self.assertEqual(rc, 1)
        self.assertEqual(T.list_tickets(self.tickets_dir), [])

    def test_new_haiku_researcher_owner_is_refused(self):
        rc = TK.main(["new", "--owner", "researcher", "--title", "Не Haiku", "--no-reviewer",
                      "--executor", "haiku", "--kind", "publish"])
        self.assertEqual(rc, 1)
        self.assertEqual(T.list_tickets(self.tickets_dir), [])

    def test_new_haiku_valid_combo_succeeds(self):
        rc = TK.main(["new", "--owner", "engineer", "--title", "Механическая", "--no-reviewer",
                      "--executor", "haiku", "--kind", "table-format"])
        self.assertEqual(rc, 0)
        tkt = T.read_ticket(T.list_tickets(self.tickets_dir)[0])
        self.assertEqual(tkt.executor, "haiku")
        self.assertEqual(tkt.kind, "table-format")
        self.assertEqual(tkt.reviewer, "")

    def test_new_kind_without_executor_is_refused(self):
        rc = TK.main(["new", "--owner", "engineer", "--title", "Странно", "--kind", "publish"])
        self.assertEqual(rc, 1)


class JudgeSimulationDecideTests(unittest.TestCase):
    """Переложение симуляций Судьи (TK-001, 27.09) на unittest — pure `decide()`, без процессов.
    Источник: `.claude/tickets/TK-001.md` «## Лог» (запись judge 21:39) и
    `docs/research/reviews/scripts/dispatcher-sim-2026-09-27.py` (номера симуляций совпадают)."""

    def setUp(self):
        self.t0 = dt("2026-09-27T22:00:00+04:00")

    def mk(self, tid, hdr, log=""):
        text = "---\n" + "\n".join(f"{k}: {v}" for k, v in hdr.items()) + "\n---\n\nописание\n\n## Лог\n" + log
        return T.parse_text(text, Path(f"{tid}.md"))

    def test_sim1_blocked_after_dispatcher_own_entry_no_longer_rewakes(self):
        """(1) обязательно: своя запись dispatcher про blocked не должна выглядеть упоминанием роли."""
        tkt = self.mk("X1", dict(id="X1", title="t", owner="researcher", status="blocked",
                                  updated=iso(self.t0)))
        # append_log пишет ЧЕРЕЗ файл — соберём текст руками, как делает симуляция Судьи
        entry_text = "Запуск роли researcher — дважды не оставил запись в «## Лог» — задача заблокирована, нужен @ceo."
        tkt.log.append(T.LogEntry(ts=self.t0, ts_raw=iso(self.t0), author="dispatcher", text=entry_text))
        state = {"sessions": {"X1::researcher": {"last_woken": iso(self.t0 - timedelta(minutes=5))}}}
        dec = D.decide(tkt, state, self.t0 + timedelta(minutes=2))
        self.assertIsNone(dec, "запись dispatcher не должна снова будить researcher на blocked-тикете")

    def test_sim2_in_progress_orphan_now_resumed(self):
        """(2) обязательно: in_progress без активного запуска и без упоминания — раньше замирал навсегда."""
        tkt = self.mk("X2", dict(id="X2", title="t", owner="engineer", status="in_progress",
                                  updated=iso(self.t0)), f"### {iso(self.t0)} engineer\nсделал шаг 1, дальше шаг 2\n")
        dec = D.decide(tkt, {}, self.t0 + timedelta(hours=3))
        self.assertEqual((dec.role, dec.reason), ("engineer", "in_progress-resume"))

    def test_sim3_review_accepted_then_done_no_longer_rewakes_judge(self):
        """(3) обязательно: ревьюер написал «принято» и поставил done — не будить его снова."""
        tkt = self.mk("X3", dict(id="X3", title="t", owner="researcher", status="in_review",
                                  reviewer="judge", updated=iso(self.t0)))
        t1 = self.t0 + timedelta(minutes=10)
        tkt.log.append(T.LogEntry(ts=t1, ts_raw=iso(t1), author="judge", text="принято @ceo"))
        tkt.header["status"] = "done"  # write_header_updates(now=t1+30s) в реальности — updated новее записи
        state = {"sessions": {"X3::judge": {"last_woken": iso(self.t0)}}}
        dec = D.decide(tkt, state, t1 + timedelta(minutes=2))
        self.assertIsNone(dec, "последняя запись лога — самого ревьюера, повторный вызов не нужен")

    def test_sim3b_review_left_in_review_is_not_touched_by_rule_g(self):
        """(3b) судья прокомментировал, но не поставил done — правило (г) не про этот статус вообще."""
        tkt = self.mk("X3b", dict(id="X3b", title="t", owner="researcher", status="in_review",
                                   reviewer="judge", updated=iso(self.t0)))
        t1 = self.t0 + timedelta(minutes=10)
        tkt.log.append(T.LogEntry(ts=t1, ts_raw=iso(t1), author="judge", text="принято"))
        state = {"sessions": {"X3b::judge": {"last_woken": iso(self.t0)}}}
        dec = D.decide(tkt, state, t1 + timedelta(minutes=2))
        self.assertIsNone(dec)

    def test_sim7_self_mention_does_not_rewake_author(self):
        """(7) можно потом: роль напоминает сама себе — не должна запускать сама себя повторно."""
        tkt = self.mk("X7", dict(id="X7", title="t", owner="researcher", status="in_review",
                                  reviewer="judge", updated=iso(self.t0)),
                       f"### {iso(self.t0 + timedelta(minutes=5))} researcher\n"
                       "сделал; напоминание себе: @researcher завтра проверить\n")
        state = {"sessions": {"X7::researcher": {"last_woken": iso(self.t0)}}}
        dec = D.decide(tkt, state, self.t0 + timedelta(minutes=6))
        # researcher не владелец решения (в), reviewer=judge, status=in_review — ни одно правило не должно
        # сработать САМО НА researcher из-за самоупоминания; judge не упомянут вовсе
        self.assertIsNone(dec)

    def test_ticket_wait_for_condition(self):
        """«Можно потом»: `wait_for: ticket:<ID>` — зависимость от другого тикета (раньше жила прозой)."""
        with tempfile.TemporaryDirectory() as d:
            tdir = Path(d)
            orig_dir = D.TICKETS_DIR
            D.TICKETS_DIR = tdir
            try:
                T.create_ticket(tdir, owner="engineer", title="Блокер", status="in_progress")
                blocker = T.list_tickets(tdir)[0]
                text = (f"---\nid: X8\nowner: researcher\nstatus: waiting\nwait_for: ticket:{blocker.stem}\n"
                        f"updated: {iso(self.t0)}\n---\n\n## Лог\n")
                tkt = T.parse_text(text, Path("X8.md"))
                self.assertIsNone(D.decide(tkt, {}, self.t0))  # блокер ещё не done
                T.write_header_updates(blocker, {"status": "done"})
                self.assertEqual(D.decide(tkt, {}, self.t0).reason, "wait_for-met")
            finally:
                D.TICKETS_DIR = orig_dir


def iso(d):
    return d.isoformat(timespec="seconds")


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


class LogLenMigrationTests(unittest.TestCase):
    """v1.5.2 (CEO 28.09, перед загрузкой bf121da): у сессий/активных запусков старого диспетчера
    (схема `last_woken`, без `log_len_at_launch`) — decide() иначе берёт 0 и правило (б) на первом
    тике после перезапуска будит все роли на ВСЕ исторические @упоминания (холостые запуски)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tickets_dir = Path(self.tmp.name) / "tickets"
        self.tickets_dir.mkdir(parents=True)
        self._orig_tickets_dir = D.TICKETS_DIR
        D.TICKETS_DIR = self.tickets_dir

    def tearDown(self):
        D.TICKETS_DIR = self._orig_tickets_dir
        self.tmp.cleanup()

    def write_ticket(self, tid, log_body):
        text = (f"---\nid: {tid}\nowner: researcher\nstatus: waiting\n"
                f"updated: 2026-09-27T11:00:00+04:00\n---\n\n## Лог\n{log_body}")
        (self.tickets_dir / f"{tid}.md").write_text(text, encoding="utf-8")
        return T.read_ticket(self.tickets_dir / f"{tid}.md")

    def test_old_session_entry_backfilled_to_current_log_length(self):
        tkt = self.write_ticket("TK-90", "\n### 2026-09-27T11:05:00+04:00 researcher\n@judge глянь.\n")
        state = {"sessions": {"TK-90::judge": {"last_woken": "2026-09-27T11:06:00+04:00"}}}
        migrated = D.migrate_log_len_at_launch(state)
        self.assertEqual(migrated, 1)
        self.assertEqual(state["sessions"]["TK-90::judge"]["log_len_at_launch"], len(tkt.log_raw))

    def test_already_migrated_entry_is_left_untouched(self):
        self.write_ticket("TK-91", "\n### 2026-09-27T11:05:00+04:00 researcher\n@judge глянь.\n")
        state = {"sessions": {"TK-91::judge": {"log_len_at_launch": 5}}}
        migrated = D.migrate_log_len_at_launch(state)
        self.assertEqual(migrated, 0)
        self.assertEqual(state["sessions"]["TK-91::judge"]["log_len_at_launch"], 5)

    def test_active_run_entry_also_backfilled(self):
        tkt = self.write_ticket("TK-92", "\n### 2026-09-27T11:05:00+04:00 researcher\nработаю.\n")
        state = {"active_runs": {"TK-92": {"role": "researcher", "pid": 123}}}
        migrated = D.migrate_log_len_at_launch(state)
        self.assertEqual(migrated, 1)
        self.assertEqual(state["active_runs"]["TK-92"]["log_len_at_launch"], len(tkt.log_raw))

    def test_missing_ticket_file_is_skipped_without_crash(self):
        state = {"sessions": {"TK-ghost::judge": {"last_woken": "x"}}}
        migrated = D.migrate_log_len_at_launch(state)
        self.assertEqual(migrated, 0)
        self.assertNotIn("log_len_at_launch", state["sessions"]["TK-ghost::judge"])

    def test_no_missing_entries_returns_zero_and_reads_no_tickets(self):
        state = {"sessions": {"TK-93::judge": {"log_len_at_launch": 5}},
                 "active_runs": {"TK-94": {"log_len_at_launch": 9}}}
        # ни один тикет TK-93/TK-94 не создан на диске — если бы функция их читала, упала бы; она
        # должна выйти раньше по пустому missing_tids
        self.assertEqual(D.migrate_log_len_at_launch(state), 0)

    def test_decide_no_longer_refires_old_mention_after_migration(self):
        """До миграции decide() видит log_len_at_launch=0 (default) и будит на старое упоминание —
        это и есть баг CEO 28.09; после миграции — тот же тикет больше не будит."""
        tkt = self.write_ticket("TK-95", "\n### 2026-09-27T11:05:00+04:00 researcher\n@judge глянь.\n")
        old_schema_state = {"sessions": {"TK-95::judge": {"last_woken": "2026-09-27T11:06:00+04:00"}}}
        # воспроизводим баг: без поля decide() берёт 0 → будит
        dec_before = D.decide(tkt, old_schema_state, dt("2026-09-27T12:00:00+04:00"))
        self.assertEqual((dec_before.role, dec_before.reason), ("judge", "mention"))
        D.migrate_log_len_at_launch(old_schema_state)
        dec_after = D.decide(tkt, old_schema_state, dt("2026-09-27T12:00:00+04:00"))
        self.assertIsNone(dec_after)


class MoneyControlsTests(unittest.TestCase):
    """v1.3 (владелец 27.09): модель/усилие, бюджет задачи, потолок запуска, часовое окно, холостой ход.
    Чистые функции — без процессов и без сети; сквозные (--max-budget-usd, cost-фолбэк, blocked) — в
    DispatchRunTests (test_launch_run_sets_model_effort_and_budget_cap, test_money_* ниже)."""

    def setUp(self):
        self.state = {}
        self.now = dt("2026-09-27T12:00:00+04:00")
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_inbox, self._orig_wake = D.CEO_INBOX, D.CEO_WAKE_LOG
        D.CEO_INBOX = Path(self.tmp.name) / "ceo-inbox.md"
        D.CEO_WAKE_LOG = Path(self.tmp.name) / "ceo-wake.log"

    def tearDown(self):
        D.CEO_INBOX, D.CEO_WAKE_LOG = self._orig_inbox, self._orig_wake
        self.tmp.cleanup()

    # --- v1.4, судья TK-002 п.3 (взамен привратника TypeSafe): таблица правил кодом ---

    def test_classify_signal_defaults_to_wake(self):
        for kind in ("blocked", "needs_owner", "budget", "hour-budget", "no-reviewer",
                     "budget-check", "mention", "parse-error", "какой-то-новый-вид-никто-не-обновил-таблицу"):
            self.assertEqual(D.classify_signal(kind), "wake", kind)

    def test_classify_signal_model_is_summary(self):
        self.assertEqual(D.classify_signal("model"), "summary")

    def test_route_ceo_signal_wake_kind_appears_immediately(self):
        D.route_ceo_signal("TK-1", "blocked", "тест", self.state, self.now)
        self.assertIn("тест", D.CEO_INBOX.read_text(encoding="utf-8"))

    def test_route_ceo_signal_summary_kind_not_lost_flushes_eventually(self):
        """Ничего не выкидывается в «только журнал» — просто уходит не сразу, а пачкой."""
        D.route_ceo_signal("TK-1", "model", "не-opus модель X", self.state, self.now)
        # первый флаш случается сразу (нет last_summary_flush) — но проверим, что текст ГДЕ-ТО есть
        self.assertTrue(D.CEO_INBOX.exists())
        self.assertIn("не-opus модель X", D.CEO_INBOX.read_text(encoding="utf-8"))

    def test_route_ceo_signal_summary_batches_within_window(self):
        D.flush_pending_summary(self.state, self.now)  # задаём точку отсчёта окна (пусто — просто маркер)
        D.route_ceo_signal("TK-1", "model", "первая", self.state, self.now + timedelta(minutes=1))
        before = D.CEO_INBOX.read_text(encoding="utf-8") if D.CEO_INBOX.exists() else ""
        D.route_ceo_signal("TK-2", "model", "вторая", self.state, self.now + timedelta(minutes=2))
        after_immediate = D.CEO_INBOX.read_text(encoding="utf-8") if D.CEO_INBOX.exists() else ""
        self.assertEqual(before, after_immediate, "вторая копится, не уходит немедленно внутри окна")
        D.route_ceo_signal("TK-3", "model", "третья", self.state,
                            self.now + timedelta(hours=D.SUMMARY_EVERY_HOURS, minutes=5))
        final = D.CEO_INBOX.read_text(encoding="utf-8")
        self.assertIn("вторая", final)
        self.assertIn("третья", final)

    # --- v1.4, судья TK-002 п.5: executor: haiku ---

    def test_haiku_refused_reason_none_for_non_haiku(self):
        text = ("---\nid: X1\nowner: engineer\nstatus: todo\nupdated: 2026-09-27T12:00:00+04:00\n---\n\n## Лог\n")
        self.assertIsNone(D.haiku_refused_reason(T.parse_text(text, Path("X1.md"))))

    def test_haiku_refused_reason_bad_kind(self):
        text = ("---\nid: X2\nowner: engineer\nstatus: todo\nexecutor: haiku\nkind: something-else\n"
                "updated: 2026-09-27T12:00:00+04:00\n---\n\n## Лог\n")
        reason = D.haiku_refused_reason(T.parse_text(text, Path("X2.md")))
        self.assertIsNotNone(reason)
        self.assertIn("kind", reason)

    def test_haiku_refused_reason_reviewer_judge(self):
        text = ("---\nid: X3\nowner: engineer\nstatus: todo\nexecutor: haiku\nkind: file-move\nreviewer: judge\n"
                "updated: 2026-09-27T12:00:00+04:00\n---\n\n## Лог\n")
        reason = D.haiku_refused_reason(T.parse_text(text, Path("X3.md")))
        self.assertIsNotNone(reason)
        self.assertIn("judge", reason)

    def test_haiku_refused_reason_owner_researcher(self):
        text = ("---\nid: X4\nowner: researcher\nstatus: todo\nexecutor: haiku\nkind: publish\n"
                "updated: 2026-09-27T12:00:00+04:00\n---\n\n## Лог\n")
        reason = D.haiku_refused_reason(T.parse_text(text, Path("X4.md")))
        self.assertIsNotNone(reason)
        self.assertIn("researcher", reason)

    def test_haiku_refused_reason_allowed_case(self):
        text = ("---\nid: X5\nowner: engineer\nstatus: todo\nexecutor: haiku\nkind: table-format\n"
                "updated: 2026-09-27T12:00:00+04:00\n---\n\n## Лог\n")
        self.assertIsNone(D.haiku_refused_reason(T.parse_text(text, Path("X5.md"))))

    # --- v1.4, судья TK-002 п.1: разница по session_id, не кумулятивный итог ---

    def test_resolve_run_cost_first_call_takes_total_as_is(self):
        """(б) нет прошлого итога → берём итог как есть, помечаем."""
        result = {"session_id": "s1", "total_cost_usd": 6.7985, "modelUsage": {"claude-sonnet-5-5": {"costUSD": 6.7985}}}
        cost, diff, note = D.resolve_run_cost(self.state, result)
        self.assertAlmostEqual(cost, 6.7985)
        self.assertTrue(note)
        self.assertEqual(self.state["session_cost_seen"]["s1"], 6.7985)

    def test_resolve_run_cost_second_call_is_diff_not_cumulative(self):
        """Живой случай 27.09: сессия судьи $6,80 → $8,29 → $8,74 кумулятивных, реально — $1,49 и $0,45."""
        r1 = {"session_id": "s1", "total_cost_usd": 6.798510249999997,
              "modelUsage": {"claude-fable-5-1": {"costUSD": 6.249625249999998},
                              "claude-sonnet-5-5": {"costUSD": 0.5488850000000001}}}
        r2 = {"session_id": "s1", "total_cost_usd": 8.289092449999998,
              "modelUsage": {"claude-fable-5-1": {"costUSD": 6.249625249999998},
                              "claude-sonnet-5-5": {"costUSD": 2.0394672000000003}}}
        r3 = {"session_id": "s1", "total_cost_usd": 8.735297849999998,
              "modelUsage": {"claude-fable-5-1": {"costUSD": 6.249625249999998},
                              "claude-sonnet-5-5": {"costUSD": 2.4856726000000005}}}
        cost1, diff1, note1 = D.resolve_run_cost(self.state, r1)
        cost2, diff2, note2 = D.resolve_run_cost(self.state, r2)
        cost3, diff3, note3 = D.resolve_run_cost(self.state, r3)
        self.assertAlmostEqual(cost2, 1.490582200000001, places=6)
        self.assertAlmostEqual(cost3, 0.446205400000000, places=6)
        self.assertFalse(note2)
        self.assertFalse(note3)
        # инвариант судьи (г): сумма разниц = последний итог
        self.assertAlmostEqual(cost1 + cost2 + cost3, r3["total_cost_usd"], places=6)
        # (в) fable не рос — не в разнице; sonnet рос — в разнице (ложной тревоги «не-sonnet» нет)
        self.assertNotIn("claude-fable-5-1", diff2)
        self.assertNotIn("claude-fable-5-1", diff3)
        self.assertIn("claude-sonnet-5-5", diff2)
        self.assertIsNone(D._model_usage_warning(diff2))
        self.assertIsNone(D._model_usage_warning(diff3))

    def test_resolve_run_cost_negative_diff_takes_total_as_is(self):
        """(б) разница < 0 (например счётчик сброшен на стороне API) → берём итог как есть, помечаем."""
        D.resolve_run_cost(self.state, {"session_id": "s1", "total_cost_usd": 5.0})
        cost, diff, note = D.resolve_run_cost(self.state, {"session_id": "s1", "total_cost_usd": 1.0})
        self.assertAlmostEqual(cost, 1.0)
        self.assertTrue(note)

    def test_resolve_run_cost_rotation_starts_fresh(self):
        """(а) разница — по session_id, не «хранилищу роли»: новая сессия после ротации начинает с нуля."""
        D.resolve_run_cost(self.state, {"session_id": "old-sid", "total_cost_usd": 20.0})
        cost, diff, note = D.resolve_run_cost(self.state, {"session_id": "new-sid-after-rotation",
                                                             "total_cost_usd": 0.5})
        self.assertAlmostEqual(cost, 0.5)  # не 0.5 - 20.0 — это другая сессия
        self.assertTrue(note)

    # --- п.1: модель/усилие ---

    def test_role_effort_mapping(self):
        # В-153: все роли — xhigh (тест в окружении без ALPHA_DISPATCH_EFFORT)
        if not os.environ.get("ALPHA_DISPATCH_EFFORT"):
            self.assertEqual(D.ROLE_EFFORT, {"judge": "xhigh", "engineer": "xhigh", "researcher": "xhigh"})

    def test_role_effort_env_override(self):
        base = {"judge": "xhigh", "engineer": "xhigh", "researcher": "xhigh"}
        self.assertEqual(D._parse_role_map("judge:xhigh,engineer:high", base),
                         {"judge": "xhigh", "engineer": "high", "researcher": "xhigh"})
        self.assertEqual(D._parse_role_map("high", base),
                         {"judge": "high", "engineer": "high", "researcher": "high"})
        self.assertEqual(D._parse_role_map("", base), base)
        self.assertEqual(base["engineer"], "xhigh")  # исходный словарь не меняется

    def test_role_model_defaults_and_env_override(self):
        # v1.6.1: Судья — Opus 5.5 (проверка всех не ослабляется), остальные — CLAUDE_MODEL
        if not os.environ.get("ALPHA_DISPATCH_ROLE_MODEL"):
            self.assertEqual(D.ROLE_MODEL, {"judge": "claude-opus-5-5", "engineer": D.CLAUDE_MODEL,
                                            "researcher": D.CLAUDE_MODEL})
        base = {"judge": "claude-opus-5-5", "engineer": "claude-sonnet-5-5", "researcher": "claude-sonnet-5-5"}
        self.assertEqual(D._parse_role_map("judge:claude-opus-5-5,engineer:claude-opus-5-5", base),
                         {"judge": "claude-opus-5-5", "engineer": "claude-opus-5-5",
                          "researcher": "claude-sonnet-5-5"})
        self.assertEqual(D._parse_role_map("claude-sonnet-5-5", base),
                         {"judge": "claude-sonnet-5-5", "engineer": "claude-sonnet-5-5",
                          "researcher": "claude-sonnet-5-5"})
        self.assertEqual(base["judge"], "claude-opus-5-5")  # исходный словарь не меняется

    def test_expected_model_family_follows_role_model(self):
        orig_role_model = D.ROLE_MODEL
        D.ROLE_MODEL = {"judge": "claude-opus-5-5", "engineer": "claude-sonnet-5-5",
                        "researcher": "claude-sonnet-5-5"}
        try:
            self.assertEqual(D._expected_model_family({"role": "judge"}), "opus")
            self.assertEqual(D._expected_model_family({"role": "engineer"}), "sonnet")
            self.assertEqual(D._expected_model_family({"role": "researcher"}), "sonnet")
            # роль вне словаря — семейство общего CLAUDE_MODEL; executor haiku — «haiku» при любой роли
            self.assertEqual(D._expected_model_family({"role": "ceo"}), D.model_family(D.CLAUDE_MODEL))
            self.assertEqual(D._expected_model_family({"role": "judge", "executor": "haiku"}), "haiku")
            # на практике: судья на opus — тишина; тот же opus у инженера — тревога (и наоборот)
            usage = {"claude-opus-5-5": {"cost": 1.0}}
            self.assertIsNone(D._model_usage_warning(usage, D._expected_model_family({"role": "judge"})))
            self.assertIsNotNone(D._model_usage_warning(usage, D._expected_model_family({"role": "engineer"})))
            usage = {"claude-sonnet-5-5": {"cost": 1.0}}
            self.assertIsNone(D._model_usage_warning(usage, D._expected_model_family({"role": "engineer"})))
            self.assertIsNotNone(D._model_usage_warning(usage, D._expected_model_family({"role": "judge"})))
        finally:
            D.ROLE_MODEL = orig_role_model

    def test_model_family_from_id(self):
        self.assertEqual(D.model_family("claude-sonnet-5-5"), "sonnet")
        self.assertEqual(D.model_family("claude-opus-5-5"), "opus")
        self.assertEqual(D.model_family("claude-haiku-4-5-20251001"), "haiku")
        self.assertEqual(D.model_family("Fable-5-1"), "fable-5-1")
        # ожидаемое по умолчанию — семейство CLAUDE_MODEL, а не жёстко «opus»
        fam = D.model_family(D.CLAUDE_MODEL)
        self.assertIsNone(D._model_usage_warning({f"claude-{fam}-x": {"cost": 1.0}}))
        other = "opus" if fam != "opus" else "sonnet"
        self.assertIsNotNone(D._model_usage_warning({f"claude-{other}-x": {"cost": 1.0}}))

    def test_model_usage_warning_none_when_absent_or_empty(self):
        # v1.4: принимает уже РАЗНИЦУ modelUsage (_model_usage_diff), не сырой результат
        self.assertIsNone(D._model_usage_warning({}))
        self.assertIsNone(D._model_usage_warning(None))
        self.assertIsNone(D._model_usage_warning("not-a-dict"))

    def test_model_usage_warning_silent_for_expected_family_only(self):
        self.assertIsNone(D._model_usage_warning({"claude-sonnet-5-5": {"cost": 1.2}}))

    def test_model_usage_warning_flags_other_family(self):
        warn = D._model_usage_warning({"fable-5-1": {"cost": 6.8}})
        self.assertIsNotNone(warn)
        self.assertIn("fable-5-1", warn)

    def test_model_usage_diff_ignores_unchanged_historical_model(self):
        """v1.4 (судья TK-002 п.1в, живой прогон 27.09): модель, не выросшая с прошлого раза —
        историческая примесь (например Fable из первого домодельного вызова сессии), не тревога."""
        current = {"claude-fable-5-1": {"costUSD": 6.25}, "claude-sonnet-5-5": {"costUSD": 2.49}}
        previous = {"claude-fable-5-1": {"costUSD": 6.25}, "claude-sonnet-5-5": {"costUSD": 2.04}}
        diff = D._model_usage_diff(current, previous)
        self.assertNotIn("claude-fable-5-1", diff)
        self.assertIn("claude-sonnet-5-5", diff)
        self.assertIsNone(D._model_usage_warning(diff))  # sonnet вырос — тревоги нет, это ожидаемая модель

    def test_model_usage_diff_flags_new_other_family_model(self):
        current = {"claude-fable-5-1": {"costUSD": 1.0}}
        diff = D._model_usage_diff(current, {})
        self.assertIn("claude-fable-5-1", diff)
        self.assertIsNotNone(D._model_usage_warning(diff))

    # --- п.2: бюджет задачи (только state.json — роли не видно) ---

    def test_parse_budget_arg_presets_and_number(self):
        self.assertEqual(D.parse_budget_arg("S"), 3.0)
        self.assertEqual(D.parse_budget_arg("m"), 10.0)
        self.assertEqual(D.parse_budget_arg("L"), 25.0)
        self.assertEqual(D.parse_budget_arg("17.5"), 17.5)
        with self.assertRaises(ValueError):
            D.parse_budget_arg("не число")

    def test_ticket_budget_default_is_m(self):
        self.assertEqual(D.ticket_budget_usd(self.state, "TK-1"), D.BUDGET_PRESETS["M"])

    def test_ticket_budget_set_and_spend(self):
        D.set_ticket_budget(self.state, "TK-1", 5.0)
        self.assertEqual(D.ticket_budget_usd(self.state, "TK-1"), 5.0)
        self.assertFalse(D.ticket_budget_exceeded(self.state, "TK-1"))
        D.add_ticket_cost(self.state, "TK-1", 3.0)
        self.assertFalse(D.ticket_budget_exceeded(self.state, "TK-1"))
        D.add_ticket_cost(self.state, "TK-1", 2.5)
        self.assertTrue(D.ticket_budget_exceeded(self.state, "TK-1"))
        self.assertAlmostEqual(D.ticket_cost_spent(self.state, "TK-1"), 5.5)

    def test_ticket_budget_isolated_per_ticket(self):
        D.set_ticket_budget(self.state, "TK-1", 3.0)
        D.add_ticket_cost(self.state, "TK-2", 100.0)
        self.assertFalse(D.ticket_budget_exceeded(self.state, "TK-1"))

    def test_notify_ticket_budget_exceeded_sets_needs_owner(self):
        with tempfile.TemporaryDirectory() as d:
            path = T.create_ticket(Path(d), owner="researcher", title="Дорогая")
            D.set_ticket_budget(self.state, path.stem, 5.0)
            D.add_ticket_cost(self.state, path.stem, 6.0)
            D.notify_ticket_budget_exceeded(path, path.stem, self.state, self.now)
            self.assertEqual(T.read_ticket(path).status, "needs_owner")
            self.assertIn("бюджет задачи исчерпан", D.CEO_INBOX.read_text(encoding="utf-8"))

    def test_notify_ticket_budget_exceeded_silent_under_budget(self):
        with tempfile.TemporaryDirectory() as d:
            path = T.create_ticket(Path(d), owner="researcher", title="Норм")
            D.set_ticket_budget(self.state, path.stem, 5.0)
            D.add_ticket_cost(self.state, path.stem, 1.0)
            D.notify_ticket_budget_exceeded(path, path.stem, self.state, self.now)
            self.assertEqual(T.read_ticket(path).status, "todo")
            self.assertFalse(D.CEO_INBOX.exists())

    def test_budget_proportionality_silent_under_80_percent(self):
        D.set_ticket_budget(self.state, "TK-1", 10.0)
        D.add_ticket_cost(self.state, "TK-1", 7.9)
        D.notify_budget_proportionality("TK-1", self.state, self.now)
        self.assertFalse(D.CEO_INBOX.exists())

    def test_budget_proportionality_flags_at_80_percent(self):
        D.set_ticket_budget(self.state, "TK-1", 10.0)
        D.add_ticket_cost(self.state, "TK-1", 8.0)
        D.notify_budget_proportionality("TK-1", self.state, self.now)
        self.assertIn("проверить соразмерность", D.CEO_INBOX.read_text(encoding="utf-8"))

    def test_budget_proportionality_notifies_once(self):
        D.set_ticket_budget(self.state, "TK-1", 10.0)
        D.add_ticket_cost(self.state, "TK-1", 9.0)
        D.notify_budget_proportionality("TK-1", self.state, self.now)
        D.notify_budget_proportionality("TK-1", self.state, self.now)
        self.assertEqual(D.CEO_INBOX.read_text(encoding="utf-8").count("проверить соразмерность"), 1)

    # --- п.4: часовое окно по всем ролям ---

    def test_hour_window_pauses_and_resumes(self):
        D._record_cost_event(self.state, self.now, D.HOUR_COST_USD)
        self.assertTrue(D._hour_budget_exceeded(self.state, self.now))
        later = self.now + timedelta(hours=1, minutes=1)
        self.assertFalse(D._hour_budget_exceeded(self.state, later), "час прошёл — окно очистилось")

    def test_hour_window_sums_across_tickets(self):
        D._record_cost_event(self.state, self.now, D.HOUR_COST_USD / 2)
        D._record_cost_event(self.state, self.now + timedelta(minutes=1), D.HOUR_COST_USD / 2 + 0.01)
        self.assertTrue(D._hour_budget_exceeded(self.state, self.now + timedelta(minutes=2)))

    def test_notify_hour_budget_only_on_transition(self):
        D._record_cost_event(self.state, self.now, D.HOUR_COST_USD)
        self.assertTrue(D._notify_hour_budget(self.state, self.now))
        inbox_after_first = D.CEO_INBOX.read_text(encoding="utf-8") if D.CEO_INBOX.exists() else ""
        self.assertTrue(D._notify_hour_budget(self.state, self.now))  # всё ещё превышено — без новой строки
        inbox_after_second = D.CEO_INBOX.read_text(encoding="utf-8") if D.CEO_INBOX.exists() else ""
        self.assertEqual(inbox_after_first, inbox_after_second)


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

    def setUp(self):
        D._DECK_CACHE.clear()

    def tearDown(self):
        D._DECK_CACHE.clear()

    def test_repeated_checks_within_cache_window_hit_ssh_once(self):
        """«Можно потом»: без кэша ssh дёргается на каждый ждущий тикет каждые 15 с."""
        calls = []

        class FakeResult:
            returncode = 0

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeResult()

        fake_clock = [1000.0]
        orig_run, orig_time = D.subprocess.run, D.time.time
        D.subprocess.run = fake_run
        D.time.time = lambda: fake_clock[0]
        try:
            self.assertTrue(D._deck_file_exists("~/alpha/queue/STATUS"))
            fake_clock[0] += D.DECK_CHECK_CACHE_S / 2  # ещё внутри окна кэша
            self.assertTrue(D._deck_file_exists("~/alpha/queue/STATUS"))
            self.assertEqual(len(calls), 1, "второй вызов внутри окна кэша не должен дёргать ssh")
            fake_clock[0] += D.DECK_CHECK_CACHE_S + 1  # окно истекло
            self.assertTrue(D._deck_file_exists("~/alpha/queue/STATUS"))
            self.assertEqual(len(calls), 2, "после истечения окна кэша — новый вызов")
        finally:
            D.subprocess.run = orig_run
            D.time.time = orig_time

    def test_tilde_path_not_quoted_away(self):
        """Живой прогон 27.09 поймал: shlex.quote('~/x') = "'~/x'" — remote-шелл её не раскрывает."""
        self.assertEqual(D._remote_test_arg("~/alpha/queue/STATUS"), "~/alpha/queue/STATUS")
        self.assertEqual(D._remote_test_arg("~"), "~")

    def test_tilde_nested_job_marker_path(self):
        """CEO 27.09: wait_for: deck: с маркером ~/alpha/queue/done/<id>.job — вложенный путь, фикс
        v1.1 общий для любой глубины после ~/, не только однокомпонентных путей."""
        arg = D._remote_test_arg("~/alpha/queue/done/T-38.job")
        self.assertEqual(arg, "~/alpha/queue/done/T-38.job")  # безопасные символы — без кавычек

    def test_wait_for_deck_job_marker_used_via_check_wait_for(self):
        calls = []

        def fake_deck_file_exists(remote_path):
            calls.append(remote_path)
            return True

        orig = D._deck_file_exists
        D._deck_file_exists = fake_deck_file_exists
        try:
            self.assertTrue(D.check_wait_for("deck:~/alpha/queue/done/T-38.job"))
        finally:
            D._deck_file_exists = orig
        self.assertEqual(calls, ["~/alpha/queue/done/T-38.job"])

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


HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"


class RoleMemoryHookTests(unittest.TestCase):
    """v1.1 (судья 27.09, п.4 «обязательно»): current_role() — сначала ALPHA_ROLE, как в
    role_context.py, иначе (если launch_run не снял CLAUDE_CODE_HOST_SESSION_ID) все роли считаются
    за CEO — тревоги/inbox/«молчание» ломаются на всех."""

    def setUp(self):
        sys.path.insert(0, str(HOOKS_DIR))
        import role_memory as rm
        self.rm = rm
        self._orig_alpha_role = os.environ.get("ALPHA_ROLE")
        self._orig_host_id = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")

    def tearDown(self):
        for key, val in (("ALPHA_ROLE", self._orig_alpha_role), ("CLAUDE_CODE_HOST_SESSION_ID", self._orig_host_id)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    def test_alpha_role_wins_even_with_ceo_host_session_id_present(self):
        os.environ["ALPHA_ROLE"] = "judge"
        os.environ["CLAUDE_CODE_HOST_SESSION_ID"] = "local_ceo-host-id-fake"
        title, role = self.rm.current_role()
        self.assertEqual(role, "judge")
        self.assertIn("judge", title)

    def test_no_alpha_role_falls_back_to_host_session_lookup(self):
        os.environ.pop("ALPHA_ROLE", None)
        os.environ.pop("CLAUDE_CODE_HOST_SESSION_ID", None)
        # без host_id find_title() не находит ничего — (None, None), не падает
        self.assertEqual(self.rm.current_role(), (None, None))

    def test_unknown_alpha_role_value_falls_back(self):
        os.environ["ALPHA_ROLE"] = "not-a-real-role"
        os.environ.pop("CLAUDE_CODE_HOST_SESSION_ID", None)
        self.assertEqual(self.rm.current_role(), (None, None))


if __name__ == "__main__":
    unittest.main()
