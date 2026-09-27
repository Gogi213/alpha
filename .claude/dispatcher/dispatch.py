"""Диспетчер задач alpha (по образцу Paperclip) — вместо постоянных чатов ролей.

Цикл раз в `POLL_INTERVAL` секунд читает `.claude/tickets/*.md` и решает, кого будить:
роль-исполнителя (`claude -p ... --resume <session_id>`) или CEO (строка в `ceo-inbox.md`).
Только stdlib. Подробности формата — `ticket.py`, правила — `README.md`.

Тест: `python -m unittest .claude/dispatcher/test_dispatch.py` (или из каталога — см. README).
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ticket as T  # noqa: E402

# --- конфигурация (константы — тесты подменяют их прямо на модуле) ------------------------

DISPATCHER_DIR = Path(__file__).resolve().parent
CLAUDE_DIR = DISPATCHER_DIR.parent
PROJECT_ROOT = CLAUDE_DIR.parent
TICKETS_DIR = PROJECT_ROOT / ".claude" / "tickets"
STATE_FILE = DISPATCHER_DIR / "state.json"
RUNS_DIR = DISPATCHER_DIR / "runs"
RUNS_LOG = DISPATCHER_DIR / "runs.log"
CEO_INBOX = DISPATCHER_DIR / "ceo-inbox.md"

CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or r"C:\Users\Георгий\.local\bin\claude"
POLL_INTERVAL = float(os.environ.get("ALPHA_DISPATCH_INTERVAL", "15"))
MAX_PARALLEL = int(os.environ.get("ALPHA_DISPATCH_MAX_PARALLEL", "2"))
RUN_TIMEOUT = float(os.environ.get("ALPHA_DISPATCH_TIMEOUT", str(40 * 60)))

ROLE_KEYS = ("researcher", "engineer", "judge")  # роли, которых диспетчер запускает; ceo — человек/CEO-сессия

# Область сессии на роль (владелец 27.09): "ticket" — сессия на (задача, роль), --resume в пределах
# задачи (как раньше); "role" — одна долгая сессия роли на ВСЕ задачи (в промпте каждый раз названа
# текущая задача); при "role" диспетчер не запускает вторую задачу этой роли, пока не закончена первая
# (задачи роли — по очереди). Переопределяемо через ALPHA_DISPATCH_SESSION_SCOPE=judge:role,engineer:ticket.
SESSION_SCOPE = {"judge": "role", "researcher": "ticket", "engineer": "ticket"}
if os.environ.get("ALPHA_DISPATCH_SESSION_SCOPE"):
    for _pair in os.environ["ALPHA_DISPATCH_SESSION_SCOPE"].split(","):
        _role, _, _scope = _pair.partition(":")
        if _role and _scope:
            SESSION_SCOPE[_role.strip()] = _scope.strip()

# Ротация долгой сессии: если контекст прошлого запуска (input + cache_read + cache_creation, по usage
# из JSON-вывода claude) превысил это число токенов — следующий запуск роли начинает новую сессию (без
# --resume) и получает в промпте напоминание перечитать блокнот и прежние решения по нужной задаче.
ROTATE_TOKENS = int(os.environ.get("ALPHA_DISPATCH_ROTATE_TOKENS", "250000"))

PROMPT_TEMPLATE = (
    "Ты — {role} команды alpha. Устав: .claude/roles/{role}.md, блокнот: .claude/roles/notes/{role}.md. "
    "Задача: .claude/tickets/{tid}.md. Сделай следующий шаг, допиши запись в «## Лог» (что сделал, что дальше), "
    "обнови status/wait_for в шапке, если ждёшь фоновую работу — status: waiting + wait_for и выходи, не жди в сессии. "
    "Упоминай @роль, если нужен другой."
)

RUNNING = {}  # tid -> {role, popen, started, attempt, run_file, err_file, out_fh, err_fh, reason}


@dataclass
class Decision:
    role: str
    reason: str
    header_updates: dict = None


# --- состояние ------------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_FILE)


# --- wait_for ---------------------------------------------------------------------------------

def check_wait_for(spec: str) -> bool:
    spec = (spec or "").strip()
    if not spec:
        return False
    if spec.startswith("file:"):
        p = spec[len("file:"):].strip()
        path = Path(p)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.exists()
    if spec.startswith("deck:"):
        return _deck_file_exists(spec[len("deck:"):].strip())
    return False  # "mention" и незнакомые формы — сами по себе не снимаются, см. правило (б)


def _deck_file_exists(remote_path: str) -> bool:
    host = os.environ.get("ALPHA_DECK_HOST", "deck@192.168.1.49")
    key = os.environ.get("ALPHA_DECK_KEY", str(Path.home() / ".ssh" / "id_ed25519"))
    cmd = ["ssh", "-i", key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host,
           f"test -e {shlex.quote(remote_path)}"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


# --- ceo-inbox ---------------------------------------------------------------------------------

def append_ceo_inbox(tid: str, kind: str, note: str, now=None) -> None:
    CEO_INBOX.parent.mkdir(parents=True, exist_ok=True)
    with open(CEO_INBOX, "a", encoding="utf-8") as fh:
        fh.write(f"- {T.now_iso(now)} {tid} [{kind}] {note}\n")


def handle_ceo_mentions(tkt: T.Ticket, state: dict, now) -> None:
    notified = state.setdefault("ceo_mention_notified", {})
    key = f"{tkt.id}::ceo"
    cutoff = T.parse_dt(notified[key]) if key in notified else None
    for entry in tkt.log:
        if "ceo" in entry.mentions and (cutoff is None or entry.ts > cutoff):
            first_line = (entry.text.splitlines() or [""])[0][:200]
            append_ceo_inbox(tkt.id, "mention", f"{entry.author} → @ceo: {first_line}", now)
            notified[key] = T.now_iso(now)
            cutoff = T.parse_dt(notified[key])


def notify_status_for_ceo(tkt: T.Ticket, state: dict, now) -> None:
    if tkt.status not in ("blocked", "needs_owner"):
        return
    notified = state.setdefault("ceo_status_notified", {})
    marker = f"{tkt.status}@{tkt.header.get('updated', '')}"
    if notified.get(tkt.id) == marker:
        return
    reason = (tkt.log[-1].text.splitlines()[0][:200] if tkt.log else "")
    append_ceo_inbox(tkt.id, tkt.status, reason, now)
    notified[tkt.id] = marker


# --- решение --------------------------------------------------------------------------------

def decide(tkt: T.Ticket, state: dict, now) -> "Decision | None":
    tid = tkt.id
    sessions = state.setdefault("sessions", {})

    def last_woken(role):
        v = sessions.get(f"{tid}::{role}", {}).get("last_woken")
        return T.parse_dt(v) if v else None

    # (б) новая запись лога с @роль после последнего запуска этой роли по задаче
    for entry in tkt.log:
        for role in entry.mentions:
            if role not in ROLE_KEYS:
                continue
            cutoff = last_woken(role)
            if cutoff is None or entry.ts > cutoff:
                return Decision(role=role, reason="mention")

    status = tkt.status
    owner = tkt.owner

    # (а) todo → owner (роль-владелец тикета)
    if status == "todo" and owner in ROLE_KEYS:
        return Decision(role=owner, reason="todo")

    # (в) waiting и условие wait_for выполнено → owner
    if status == "waiting" and owner in ROLE_KEYS:
        if check_wait_for(tkt.header.get("wait_for", "")):
            return Decision(role=owner, reason="wait_for-met")
        return None

    # (г) done при заданном reviewer и без записи ревьюера → in_review, будит ревьюера
    if status == "done" and tkt.reviewer in ROLE_KEYS:
        reviewer = tkt.reviewer
        updated = tkt.header.get("updated")
        cutoff = T.parse_dt(updated) if updated else None
        if cutoff is None or not tkt.logged_since(reviewer, cutoff):
            return Decision(role=reviewer, reason="review", header_updates={"status": "in_review"})

    return None


# --- область сессии (SESSION_SCOPE) ------------------------------------------------------------

def _resume_store(state: dict, tid: str, role: str) -> dict:
    """session_id/токены контекста: при scope="role" — общие на роль, при "ticket" — на (задача, роль)."""
    if SESSION_SCOPE.get(role, "ticket") == "role":
        return state.setdefault("role_sessions", {}).setdefault(role, {})
    return state.setdefault("ticket_sessions", {}).setdefault(f"{tid}::{role}", {})


def _context_tokens(usage: dict) -> int:
    usage = usage or {}
    return (int(usage.get("input_tokens") or 0) + int(usage.get("cache_read_input_tokens") or 0) +
            int(usage.get("cache_creation_input_tokens") or 0))


def _role_busy(role: str) -> bool:
    """При scope="role" — идёт ли уже где-то (на другой задаче) единственная сессия этой роли."""
    if SESSION_SCOPE.get(role, "ticket") != "role":
        return False
    return any(info["role"] == role for info in RUNNING.values())


# --- запуск роли ------------------------------------------------------------------------------

def _popen(cmd, **kwargs):
    """Точка подмены для тестов (вместо многословного CLAUDE_BIN) — оборачивает subprocess.Popen."""
    return subprocess.Popen(cmd, **kwargs)


def build_prompt(role: str, tid: str, extra_note: str = None) -> str:
    prompt = PROMPT_TEMPLATE.format(role=role, tid=tid)
    if extra_note:
        prompt += " " + extra_note
    return prompt


def launch_run(ticket_path, role: str, state: dict, now, reason: str, attempt: int = 0,
               extra_note: str = None) -> None:
    ticket_path = Path(ticket_path)
    tid = ticket_path.stem
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = now.strftime("%Y%m%d-%H%M%S")
    run_file = RUNS_DIR / f"{ts}-{tid}-{role}.json"
    err_file = RUNS_DIR / f"{ts}-{tid}-{role}.err.log"

    store = _resume_store(state, tid, role)
    sid = store.get("session_id")
    if sid and store.get("last_context_tokens", 0) > ROTATE_TOKENS:
        # ротация: контекст прошлой сессии этой роли слишком большой — начинаем новую
        sid = None
        rotate_note = (f"Начинаем новую сессию (контекст прошлой превысил {ROTATE_TOKENS} токенов): "
                        f"прочитай блокнот .claude/roles/notes/{role}.md и прежние решения "
                        f"docs/research/reviews/ по нужной задаче.")
        extra_note = " ".join(x for x in (extra_note, rotate_note) if x)

    prompt = build_prompt(role, tid, extra_note)
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--permission-mode", "bypassPermissions"]
    if sid:
        cmd += ["--resume", sid]

    env = dict(os.environ)
    env["ALPHA_ROLE"] = role

    out_fh = open(run_file, "w", encoding="utf-8")
    err_fh = open(err_file, "w", encoding="utf-8")
    popen = _popen(cmd, cwd=str(PROJECT_ROOT), env=env, stdout=out_fh, stderr=err_fh, text=True)
    RUNNING[tid] = {
        "role": role, "popen": popen, "started": now, "attempt": attempt,
        "run_file": run_file, "err_file": err_file, "out_fh": out_fh, "err_fh": err_fh, "reason": reason,
    }
    # last_woken — для дедупа правила (б) «упоминание»; всегда на (задачу, роль), не зависит от SESSION_SCOPE
    state.setdefault("sessions", {}).setdefault(f"{tid}::{role}", {})["last_woken"] = T.now_iso(now)


def _read_run_result(run_file: Path) -> dict:
    try:
        data = Path(run_file).read_text(encoding="utf-8").strip()
        return json.loads(data) if data else {}
    except Exception:
        return {}


def _log_run_summary(tid: str, info: dict, result: dict, now, timed_out: bool) -> None:
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    usage = result.get("usage") or {}
    cost = result.get("total_cost_usd", "-")
    status = "timeout" if timed_out else ("ok" if result else "no_output")
    line = (f"{T.now_iso(now)} {tid} {info['role']} reason={info.get('reason')} "
            f"attempt={info.get('attempt', 0)} session={result.get('session_id', '-')} "
            f"cost_usd={cost} in_tok={usage.get('input_tokens', '-')} out_tok={usage.get('output_tokens', '-')} "
            f"ctx_tok={_context_tokens(usage)} status={status}\n")
    with open(RUNS_LOG, "a", encoding="utf-8") as fh:
        fh.write(line)


def _finish_run(tid: str, info: dict, state: dict, now, timed_out: bool) -> None:
    for fh in (info.get("out_fh"), info.get("err_fh")):
        try:
            fh.close()
        except Exception:
            pass
    result = _read_run_result(info["run_file"])
    _log_run_summary(tid, info, result, now, timed_out)

    role = info["role"]
    key = f"{tid}::{role}"
    sess = state.setdefault("sessions", {}).setdefault(key, {})  # retries — всегда на (задачу, роль)
    store = _resume_store(state, tid, role)  # session_id/токены — по SESSION_SCOPE[role]
    if result.get("session_id"):
        store["session_id"] = result["session_id"]
    store["last_context_tokens"] = _context_tokens(result.get("usage"))

    path = TICKETS_DIR / f"{tid}.md"
    if not path.exists():
        return
    tkt = T.read_ticket(path)
    ok = (not timed_out) and tkt.logged_since(role, info["started"])
    if ok:
        sess["retries"] = 0
        return

    # (д) запуск завершился, новой записи лога от роли нет
    if info.get("attempt", 0) < 1:
        note = ("Предыдущий запуск не оставил новую запись в «## Лог» — обязательно допиши итог и "
                "обнови status." if not timed_out else
                "Предыдущий запуск не уложился в таймаут — сократи шаг и обязательно запиши итог.")
        launch_run(path, role, state, now, reason="retry", attempt=info.get("attempt", 0) + 1, extra_note=note)
        sess["retries"] = sess.get("retries", 0) + 1
    else:
        T.write_header_updates(path, {"status": "blocked"}, now=now)
        T.append_log(path, "dispatcher",
                     f"Запуск роли @{role} дважды не оставил запись в «## Лог» — задача заблокирована, нужен @ceo.",
                     now=now)
        sess["retries"] = 0
        append_ceo_inbox(tid, "blocked", f"{role}: дважды без записи в лог", now)


def _poll_running(state: dict, now) -> None:
    for tid in list(RUNNING):
        info = RUNNING[tid]
        popen = info["popen"]
        if popen.poll() is None:
            if (now - info["started"]).total_seconds() > RUN_TIMEOUT:
                popen.kill()
                try:
                    popen.wait(timeout=10)
                except Exception:
                    pass
                del RUNNING[tid]
                _finish_run(tid, info, state, now, timed_out=True)
            continue
        del RUNNING[tid]
        _finish_run(tid, info, state, now, timed_out=False)


# --- тик / цикл -------------------------------------------------------------------------------

def tick(now=None) -> int:
    now = now or datetime.now().astimezone()
    state = load_state()
    _poll_running(state, now)
    save_state(state)

    launched = 0
    for path in T.list_tickets(TICKETS_DIR):
        try:
            tkt = T.read_ticket(path)
        except Exception as e:
            append_ceo_inbox(path.stem, "parse-error", f"{type(e).__name__}: {e}", now)
            continue

        handle_ceo_mentions(tkt, state, now)
        notify_status_for_ceo(tkt, state, now)

        tid = tkt.id
        if tid in RUNNING or len(RUNNING) >= MAX_PARALLEL:
            continue
        decision = decide(tkt, state, now)
        if decision is None:
            continue
        if _role_busy(decision.role):
            continue  # SESSION_SCOPE="role": у роли уже идёт другая задача — своей очереди ждём
        if decision.header_updates:
            T.write_header_updates(path, decision.header_updates, now=now)
        launch_run(path, decision.role, state, now, reason=decision.reason)
        launched += 1

    save_state(state)
    return launched


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    TICKETS_DIR.mkdir(parents=True, exist_ok=True)
    if "--once" in argv:
        n = tick()
        print(f"[dispatch] once: launched={n} running={len(RUNNING)}")
        return 0
    print(f"[dispatch] loop every {POLL_INTERVAL}s, MAX_PARALLEL={MAX_PARALLEL}, CLAUDE_BIN={CLAUDE_BIN}")
    while True:
        try:
            tick()
        except Exception as e:
            print(f"[dispatch] tick error: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
