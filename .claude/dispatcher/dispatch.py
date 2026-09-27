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
from datetime import datetime, timedelta
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
CEO_WAKE_LOG = DISPATCHER_DIR / "ceo-wake.log"  # короткая копия каждой строки ceo-inbox — CEO держит на ней Monitor

CLAUDE_BIN = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or r"C:\Users\Георгий\.local\bin\claude"
PID_EXPECT_NAME = "claude"  # _pid_alive: подстрока имени образа процесса; тесты подменяют на "python"
POLL_INTERVAL = float(os.environ.get("ALPHA_DISPATCH_INTERVAL", "15"))
MAX_PARALLEL = int(os.environ.get("ALPHA_DISPATCH_MAX_PARALLEL", "2"))
RUN_TIMEOUT = float(os.environ.get("ALPHA_DISPATCH_TIMEOUT", str(40 * 60)))

# Защита от петли и перерасхода (владелец 27.09, v1.1). MAX_RUNS_PER_TICKET_HOUR/MIN_GAP_S —
# троттлинг решений (а)-(г): тикет просто пропускается этот тик, без ceo-inbox (не ошибка, а
# пауза); ретраи правила (д) их не считают — они и так ограничены одной попыткой. DAILY_COST_USD —
# суточный (по календарной дате `now`) потолок расхода `total_cost_usd`: превышен → новые запуски
# (включая ретраи) не стартуют, одна строка в ceo-inbox на сутки.
MAX_RUNS_PER_TICKET_HOUR = int(os.environ.get("ALPHA_DISPATCH_MAX_RUNS_PER_TICKET_HOUR", "6"))
MIN_GAP_S = float(os.environ.get("ALPHA_DISPATCH_MIN_GAP_S", "60"))
DAILY_COST_USD = float(os.environ.get("ALPHA_DISPATCH_DAILY_COST_USD", "150"))

# Модель и перерасход (владелец 27.09, v1.2 — пилот Судьи на умолчаниях CLI стоил $6,8 на Fable 5.1
# xhigh): модель и усилие теперь ВСЕГДА явно в команде запуска, не полагаемся на умолчание CLI.
CLAUDE_MODEL = os.environ.get("ALPHA_DISPATCH_MODEL", "claude-opus-5-5")
ROLE_EFFORT = {"judge": "xhigh", "engineer": "high", "researcher": "high"}

# executor: haiku (судья TK-002 п.5) — механические задачи только: белый список видов (--kind при
# tickets.py new), приёмка результата — кодом (в конкретных скриптах-проверках по виду, не здесь).
# Обход Судьи запрещён (условие г): reviewer: judge или owner: researcher — не Haiku, tickets.py new
# отказывает раньше, чем тикет вообще появится; здесь — вторая защита на случай ручной правки шапки.
CLAUDE_HAIKU_MODEL = os.environ.get("ALPHA_DISPATCH_HAIKU_MODEL", "claude-haiku-4-5-20251001")
HAIKU_ALLOWED_KINDS = {"file-move", "table-format", "publish"}

# Потолок одного запуска (--max-budget-usd, встроенный флаг CLI) — min(остаток бюджета задачи, этот
# потолок). Часовая скорость трат — скользящее окно 60 мин по ВСЕМ ролям сразу (не на роль/задачу).
RUN_CAP_USD = float(os.environ.get("ALPHA_DISPATCH_RUN_CAP_USD", "8"))
HOUR_COST_USD = float(os.environ.get("ALPHA_DISPATCH_HOUR_COST_USD", "15"))

# Бюджет задачи (владелец 27.09, поправка: «запрещено добивать задачи до их бюджетов, раздувая
# токены» — bюджет и траты живут ТОЛЬКО в state.json, роль их не видит ни в шапке тикета, ни в
# промпте). `tickets.py new --budget S|M|L|<число>` пишет в state через set_ticket_budget().
BUDGET_PRESETS = {"S": 3.0, "M": 10.0, "L": 25.0}
DEFAULT_TICKET_BUDGET_USD = BUDGET_PRESETS["M"]

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
    "Задача: .claude/tickets/{tid}.md. Лимит этого запуска — {timeout_min} мин; шаг длиннее — выноси в фон "
    "(например systemd-run на Steam Deck) и ставь status: waiting + wait_for, не жди в сессии. Трать минимум: "
    "самый короткий путь к результату задачи; траты каждого запуска записываются и сравниваются с "
    "результатом. Сделай следующий шаг; запись в лог — командой "
    "`python .claude/dispatcher/tickets.py comment {tid} --author {role} --text \"...\"` (что сделал, что "
    "дальше) ДО истечения лимита — записанный частичный прогресс не провал, диспетчер продолжит с него сам; "
    "обнови status/wait_for в шапке сама (не «todo», если работа не закончена — иначе задача просто "
    "возьмётся в работу заново). Упоминай @роль, если нужен другой."
)

RUNNING = {}  # tid -> {role, popen, pid, started, attempt, run_file, err_file, out_fh, err_fh, reason}
# popen=None у записей, восстановленных из state.json["active_runs"] после перезапуска диспетчера
# (recover_active_runs) — тогда живость и остановка идут по pid (_pid_alive/_pid_kill), не по Popen.


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
    if spec.startswith("ticket:"):
        return _other_ticket_done(spec[len("ticket:"):].strip())
    return False  # "mention" и незнакомые формы — сами по себе не снимаются, см. правило (б)


def _other_ticket_done(other_id: str) -> bool:
    """`wait_for: ticket:<ID>` — ждём, пока другой тикет дойдёт до status: done (судья 27.09, «можно потом»:
    зависимости T-XX жили только прозой TASKS.md, диспетчер их не видел)."""
    other_path = TICKETS_DIR / f"{other_id}.md"
    if not other_path.exists():
        return False
    try:
        return T.read_ticket(other_path).status == "done"
    except Exception:
        return False


def _remote_test_arg(remote_path: str) -> str:
    """`test -e` аргумент: `~`/`~/...` — без кавычек вокруг тильды, иначе remote-шелл не раскроет её
    в $HOME (shlex.quote экранирует и тильду тоже — поймано боевым вызовом v1.1, 27.09)."""
    remote_path = remote_path.strip()
    if remote_path == "~":
        return "~"
    if remote_path.startswith("~/"):
        rest = remote_path[1:]  # оставляем ведущий '~' сырым, остальное — безопасно экранируем
        return "~" + shlex.quote(rest)
    return shlex.quote(remote_path)


_DECK_CACHE = {}  # remote_path -> (time.time() отметка, результат) — см. _deck_file_exists
DECK_CHECK_CACHE_S = float(os.environ.get("ALPHA_DISPATCH_DECK_CACHE_S", "60"))


def _deck_file_exists(remote_path: str) -> bool:
    # Судья 27.09 («можно потом»): без кэша ssh дёргается на каждый ждущий тикет каждые 15 с —
    # кэшируем результат на DECK_CHECK_CACHE_S, как deck_alert() в role_memory.py (15 мин там,
    # здесь короче — это условие продолжения работы, не редкая тревога).
    cached = _DECK_CACHE.get(remote_path)
    now_ts = time.time()
    if cached and (now_ts - cached[0]) < DECK_CHECK_CACHE_S:
        return cached[1]
    # Кириллический HOME на этой машине ломает умолчания ssh (В-см. windows-ssh-cyrillic-home) —
    # ключ, known_hosts и хост берём явно, не полагаясь на ~/.ssh по умолчанию.
    host = os.environ.get("ALPHA_DECK_HOST", "deck@192.168.1.49")
    key = os.environ.get("ALPHA_DECK_KEY", r"C:/Users/Георгий/.ssh/id_rsa")
    known_hosts = os.environ.get("ALPHA_DECK_KNOWN_HOSTS", r"C:/Users/Георгий/.ssh/known_hosts")
    cmd = ["ssh", "-i", key, "-o", f"UserKnownHostsFile={known_hosts}", "-o", "BatchMode=yes",
           "-o", "ConnectTimeout=8", host, f"test -e {_remote_test_arg(remote_path)}"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15)
        result = r.returncode == 0
    except Exception:
        result = False
    _DECK_CACHE[remote_path] = (now_ts, result)
    return result


# --- ceo-inbox ---------------------------------------------------------------------------------

def append_ceo_inbox(tid: str, kind: str, note: str, now=None) -> None:
    CEO_INBOX.parent.mkdir(parents=True, exist_ok=True)
    with open(CEO_INBOX, "a", encoding="utf-8") as fh:
        fh.write(f"- {T.now_iso(now)} {tid} [{kind}] {note}\n")
    # ceo-wake.log — короткая (время, задача, причина) копия для Monitor CEO; ceo-inbox.md остаётся источником деталей
    with open(CEO_WAKE_LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{T.now_iso(now)} {tid} {kind}\n")


# --- таблица правил «вид сигнала → будить / сводка» (судья TK-002 п.3, взамен привратника TypeSafe) --
#
# В-85: классифицирует КОД по виду сигнала (`kind` из append_ceo_inbox), не модель. Судья отверг
# привратник на Jev в предложенном виде — асимметрия цены ошибок (пропуск сигнала стоит часы простоя
# команды, лишнее пробуждение — центы); почти все виды здесь структурные, не свободный текст. Ничего не
# отбрасывается: "summary" копится и уходит одной строкой не реже SUMMARY_EVERY_HOURS — не молчание.
# Неизвестный вид (кто-то добавит новый append_ceo_inbox без обновления таблицы) — по умолчанию "wake",
# безопасная сторона асимметрии.
SIGNAL_SUMMARY_KINDS = {"model"}  # уже само по себе диагностика/лог, не требует немедленной реакции
SUMMARY_EVERY_HOURS = float(os.environ.get("ALPHA_DISPATCH_SUMMARY_HOURS", "1"))


def classify_signal(kind: str) -> str:
    return "summary" if kind in SIGNAL_SUMMARY_KINDS else "wake"


def flush_pending_summary(state: dict, now) -> None:
    pending = state.get("pending_summary") or []
    if pending:
        line = f"- {T.now_iso(now)} * [summary] {len(pending)} сигнал(ов): " + " | ".join(pending)
        CEO_INBOX.parent.mkdir(parents=True, exist_ok=True)
        with open(CEO_INBOX, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        with open(CEO_WAKE_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{T.now_iso(now)} * summary({len(pending)})\n")
    state["pending_summary"] = []
    state["last_summary_flush"] = T.now_iso(now)


def route_ceo_signal(tid: str, kind: str, note: str, state: dict, now) -> None:
    """append_ceo_inbox() для "wake"-видов; "summary"-виды копятся и уходят пачкой по SUMMARY_EVERY_HOURS."""
    if classify_signal(kind) != "summary":
        append_ceo_inbox(tid, kind, note, now)
        return
    pending = state.setdefault("pending_summary", [])
    pending.append(f"{T.now_iso(now)} {tid} [{kind}] {note[:150]}")
    last_flush = state.get("last_summary_flush")
    last_flush_dt = T.parse_dt(last_flush) if last_flush else None
    if last_flush_dt is None or (now - last_flush_dt) >= timedelta(hours=SUMMARY_EVERY_HOURS):
        flush_pending_summary(state, now)


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


def notify_parse_error(tid: str, err_text: str, state: dict, now) -> None:
    """Дедуп по (тикет, текст ошибки) — судья 27.09, п.5 «обязательно»: без дедупа сломанный вручную
    тикет пишет строку в ceo-inbox.md/ceo-wake.log КАЖДЫЙ тик (симуляция: 240/час при POLL_INTERVAL=15с)."""
    notified = state.setdefault("ceo_parse_error_notified", {})
    if notified.get(tid) == err_text:
        return
    append_ceo_inbox(tid, "parse-error", err_text, now)
    notified[tid] = err_text


def notify_done_without_reviewer(tkt: T.Ticket, state: dict, now) -> None:
    """Судья 27.09, п.6 «обязательно»: `done` без `reviewer` никого не уведомляет — тикет минует
    проверку Судьи молча (вопреки «числа владельцу — после Судьи»). `tickets.py new` теперь ставит
    `reviewer: judge` по умолчанию для researcher/engineer; здесь — сеть на случай явного отказа/старых
    тикетов без reviewer вовсе."""
    if tkt.status != "done" or tkt.reviewer in ROLE_KEYS:
        return
    notified = state.setdefault("ceo_no_reviewer_notified", {})
    marker = f"done@{tkt.header.get('updated', '')}"
    if notified.get(tkt.id) == marker:
        return
    append_ceo_inbox(tkt.id, "no-reviewer", "done без reviewer — числа минуют проверку Судьи", now)
    notified[tkt.id] = marker


def haiku_refused_reason(tkt: T.Ticket) -> str:
    """None — можно запускать на Haiku; иначе причина отказа. Судья TK-002 п.5: (а) белый список видов
    (kind), (г) обход проверки Судьи запрещён — reviewer: judge или owner: researcher не бывают Haiku,
    даже если tickets.py new это пропустил (ручная правка шапки) — вторая защита, уже в диспетчере."""
    if tkt.executor != "haiku":
        return None
    if tkt.kind not in HAIKU_ALLOWED_KINDS:
        return f"executor: haiku требует kind из {sorted(HAIKU_ALLOWED_KINDS)}, у тикета kind={tkt.kind or '(пусто)'}"
    if tkt.reviewer.lower() == "judge":
        return "executor: haiku нельзя вместе с reviewer: judge — числа/вердикты не на Haiku"
    if tkt.owner == "researcher":
        return "executor: haiku нельзя для owner: researcher — исследовательский результат не на Haiku"
    return None


# --- решение --------------------------------------------------------------------------------

def decide(tkt: T.Ticket, state: dict, now) -> "Decision | None":
    tid = tkt.id
    if tkt.status == "backlog":
        return None  # перенесено из TASKS.md, ещё не в работе — диспетчер не трогает; см. `tickets.py start`
    sessions = state.setdefault("sessions", {})

    # (б) новая запись лога с @роль после последнего запуска этой роли по задаче — по РОСТУ сырого
    # текста секции «## Лог» (T.mentions_since), не по разбору заголовков `### <ISO> <автор>`: роли
    # пишут по-разному (CEO 27.09, TK-005 — «- 27.09 ~23:50 (инженер, запуск 1) …» без заголовка).
    # Самоупоминание/`dispatcher`-автор исключаются там, где автор известен (см. mentions_since);
    # cутки без заголовка — упоминание не исключается (асимметрия цены ошибок).
    for role in ROLE_KEYS:
        seen_len = sessions.get(f"{tid}::{role}", {}).get("log_len_at_launch", 0)
        if role in T.mentions_since(tkt.log_raw, seen_len):
            return Decision(role=role, reason="mention")

    status = tkt.status
    owner = tkt.owner

    # (а) todo → owner (роль-владелец тикета)
    if status == "todo" and owner in ROLE_KEYS:
        return Decision(role=owner, reason="todo")

    # (а') in_progress без активного запуска → владелец, чтобы продолжить многошаговую задачу (судья
    # 27.09, п.2 «обязательно»: раньше такой тикет замирал после первой сессии — устав ролей обещает
    # продолжение другой сессией, а диспетчер никого не будил). Троттлинг — MIN_GAP_S/MAX_RUNS_PER_TICKET_HOUR
    # в tick(), как у любого решения; уходит через явную смену status (done/waiting/blocked/…).
    if status == "in_progress" and owner in ROLE_KEYS:
        return Decision(role=owner, reason="in_progress-resume")

    # (в) waiting и условие wait_for выполнено → owner
    if status == "waiting" and owner in ROLE_KEYS:
        if check_wait_for(tkt.header.get("wait_for", "")):
            return Decision(role=owner, reason="wait_for-met")
        return None

    # (г) done при заданном reviewer → in_review, будит ревьюера — но не когда последняя запись лога
    # уже от самого ревьюера (или dispatcher): это штатный конец состоявшегося ревью, не новый раунд.
    # Раньше проверялось по updated-таймстампу — тот становится новее записи ревьюера, стоит роли
    # проставить status ПОСЛЕ append_log, и (г) будило ревьюера повторно за его же вердикт (судья
    # 27.09, п.3 «обязательно»; при SESSION_SCOPE="role" это занимало единственную сессию судьи).
    if status == "done" and tkt.reviewer in ROLE_KEYS:
        reviewer = tkt.reviewer
        last_author = tkt.log[-1].author.lower() if tkt.log else None
        if last_author in (reviewer.lower(), "dispatcher"):
            return None
        return Decision(role=reviewer, reason="review", header_updates={"status": "in_review"})

    return None


# --- область сессии (SESSION_SCOPE) ------------------------------------------------------------

def _resume_store(state: dict, tid: str, role: str) -> dict:
    """session_id/токены контекста: при scope="role" — общие на роль, при "ticket" — на (задача, роль)."""
    if SESSION_SCOPE.get(role, "ticket") == "role":
        return state.setdefault("role_sessions", {}).setdefault(role, {})
    return state.setdefault("ticket_sessions", {}).setdefault(f"{tid}::{role}", {})


def _tokens_of(d: dict) -> int:
    d = d or {}
    return (int(d.get("input_tokens") or 0) + int(d.get("cache_read_input_tokens") or 0) +
            int(d.get("cache_creation_input_tokens") or 0))


def _context_tokens_sum(usage: dict) -> int:
    """Сумма input+cache_read+cache_creation по ВСЕМУ usage из JSON `claude -p` — это сумма по всем
    ходам запуска, не контекст одного хода (см. _context_tokens_last). Для runs.log (ctx_sum=)."""
    return _tokens_of(usage)


def _context_tokens_last(result: dict) -> int:
    """Контекст, который реально понесёт следующий `--resume` — последний ход ЭТОГО запуска, не сумма
    по всем его ходам: usage в JSON claude -p суммирует по ходам, и на многоходовом запуске это
    завышает контекст в разы, рвя ротацию раньше времени (CEO 27.09, живой прогон: ctx_sum=333 886
    при реальном контексте хода ~52 тыс.). usage.iterations[-1] — контекст последнего хода; нет
    iterations — запасной вариант ctx_sum // num_turns."""
    usage = result.get("usage") or {}
    iterations = usage.get("iterations")
    if isinstance(iterations, list) and iterations:
        return _tokens_of(iterations[-1])
    total = _context_tokens_sum(usage)
    try:
        num_turns = int(result.get("num_turns") or 1) or 1
    except (TypeError, ValueError):
        num_turns = 1
    return total // num_turns


def _role_busy(role: str) -> bool:
    """При scope="role" — идёт ли уже где-то (на другой задаче) единственная сессия этой роли."""
    if SESSION_SCOPE.get(role, "ticket") != "role":
        return False
    return any(info["role"] == role for info in RUNNING.values())


# --- защита от петли и перерасхода (v1.1) --------------------------------------------------

def _record_launch(state: dict, tid: str, now) -> None:
    hist = state.setdefault("launch_history", {}).setdefault(tid, [])
    hist.append(T.now_iso(now))
    cutoff = now - timedelta(hours=2)  # храним немного с запасом сверх окна MAX_RUNS_PER_TICKET_HOUR
    state["launch_history"][tid] = [t for t in hist if T.parse_dt(t) > cutoff]


def _rate_limited(state: dict, tid: str, now) -> bool:
    """MAX_RUNS_PER_TICKET_HOUR / MIN_GAP_S — троттлинг решений (а)-(г); ретраи (д) их не проходят."""
    hist = [T.parse_dt(t) for t in state.get("launch_history", {}).get(tid, [])]
    if not hist:
        return False
    if len([t for t in hist if (now - t) < timedelta(hours=1)]) >= MAX_RUNS_PER_TICKET_HOUR:
        return True
    return (now - max(hist)).total_seconds() < MIN_GAP_S


def _today(now) -> str:
    return now.strftime("%Y-%m-%d")


def _add_cost(state: dict, now, cost) -> None:
    if not cost:
        return
    daily = state.setdefault("daily_cost", {})
    day = _today(now)
    daily[day] = round(daily.get(day, 0.0) + float(cost), 6)


def _daily_budget_exceeded(state: dict, now) -> bool:
    return state.get("daily_cost", {}).get(_today(now), 0.0) >= DAILY_COST_USD


def _notify_budget_once(state: dict, now) -> None:
    day = _today(now)
    notified = state.setdefault("daily_cost_notified", {})
    if notified.get("day") == day:
        return
    cost = state.get("daily_cost", {}).get(day, 0.0)
    append_ceo_inbox("*", "budget", f"суточный потолок стоимости исчерпан: ${cost:.2f} ≥ ${DAILY_COST_USD} за {day}",
                      now)
    notified["day"] = day


def parse_budget_arg(raw: str) -> float:
    """S|M|L или число долларов — для `tickets.py new --budget`."""
    raw = (raw or "").strip().upper()
    if raw in BUDGET_PRESETS:
        return BUDGET_PRESETS[raw]
    return float(raw)


def set_ticket_budget(state: dict, tid: str, budget_usd) -> None:
    state.setdefault("ticket_budget", {})[tid] = float(budget_usd)


def ticket_budget_usd(state: dict, tid: str) -> float:
    return state.get("ticket_budget", {}).get(tid, DEFAULT_TICKET_BUDGET_USD)


def ticket_cost_spent(state: dict, tid: str) -> float:
    return state.get("ticket_cost", {}).get(tid, 0.0)


def add_ticket_cost(state: dict, tid: str, cost) -> None:
    if not cost:
        return
    costs = state.setdefault("ticket_cost", {})
    costs[tid] = round(costs.get(tid, 0.0) + float(cost), 6)


def ticket_budget_exceeded(state: dict, tid: str) -> bool:
    return ticket_cost_spent(state, tid) >= ticket_budget_usd(state, tid)


def notify_ticket_budget_exceeded(path: Path, tid: str, state: dict, now) -> None:
    """п.2: бюджет задачи исчерпан → новые запуски не стартуют, status: needs_owner, строка CEO."""
    if not ticket_budget_exceeded(state, tid):
        return
    notified = state.setdefault("ceo_ticket_budget_notified", {})
    if tid in notified:
        return
    spent = ticket_cost_spent(state, tid)
    budget = ticket_budget_usd(state, tid)
    T.write_header_updates(path, {"status": "needs_owner"}, now=now)
    append_ceo_inbox(tid, "budget", f"бюджет задачи исчерпан: потрачено ${spent:.2f} из ${budget:.2f}", now)
    notified[tid] = True


def notify_budget_proportionality(tid: str, state: dict, now) -> None:
    """Поправка владельца 27.09: при закрытии задачи (status: done) — если потрачено ≥ 80% бюджета,
    строка CEO «проверить соразмерность» (один раз на первое достижение done)."""
    notified = state.setdefault("ceo_budget_proportionality_notified", {})
    if tid in notified:
        return
    notified[tid] = True
    budget = ticket_budget_usd(state, tid)
    if budget <= 0:
        return
    frac = ticket_cost_spent(state, tid) / budget
    if frac >= 0.8:
        spent = ticket_cost_spent(state, tid)
        append_ceo_inbox(tid, "budget-check",
                          f"закрыта на {frac:.0%} бюджета (${spent:.2f} из ${budget:.2f}) — проверить соразмерность",
                          now)


def _record_cost_event(state: dict, now, cost) -> None:
    """Скользящее часовое окно по ВСЕМ ролям (п.4) — история (время, сумма), обрезаем с запасом."""
    if not cost:
        return
    hist = state.setdefault("cost_history", [])
    hist.append([T.now_iso(now), float(cost)])
    cutoff = now - timedelta(hours=2)
    state["cost_history"] = [e for e in hist if T.parse_dt(e[0]) > cutoff]


def _rolling_hour_cost(state: dict, now) -> float:
    cutoff = now - timedelta(hours=1)
    return sum(c for t, c in state.get("cost_history", []) if T.parse_dt(t) > cutoff)


def _hour_budget_exceeded(state: dict, now) -> bool:
    return _rolling_hour_cost(state, now) >= HOUR_COST_USD


def _notify_hour_budget(state: dict, now) -> bool:
    """Возвращает, стоит ли пауза по скорости; пишет строку CEO только на переходе False → True."""
    exceeded = _hour_budget_exceeded(state, now)
    was_paused = state.get("hour_cost_paused", False)
    if exceeded and not was_paused:
        cost = _rolling_hour_cost(state, now)
        append_ceo_inbox("*", "hour-budget", f"скорость трат — пауза: ${cost:.2f} за час ≥ ${HOUR_COST_USD}", now)
    state["hour_cost_paused"] = exceeded
    return exceeded


def _model_usage_number(v) -> float:
    """costUSD (или похожее поле) из одной записи modelUsage; неизвестная форма — 1.0 (сам факт есть)."""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("costUSD", "cost_usd", "cost", "totalCostUsd", "total_cost_usd"):
            if k in v:
                try:
                    return float(v[k])
                except (TypeError, ValueError):
                    return 0.0
        return 1.0
    return 0.0


def _model_usage_diff(current: dict, previous: dict) -> dict:
    """Модели, реально задействованные В ЭТОМ прогоне — не за всю историю сессии (судья TK-002 п.1в):
    --resume отдаёт modelUsage кумулятивно, и историческая примесь (например Fable из самого первого,
    домодельного вызова сессии) иначе вечно всплывает как «не-opus», хотя в этом прогоне её не было —
    поймано 27.09 на живой сессии судьи (T-38/TK-002, claude-fable-5-1 не рос ни разу после в1.2)."""
    current = current or {}
    previous = previous or {}
    diff = {}
    for model, val in current.items():
        cur_n = _model_usage_number(val)
        prev_n = _model_usage_number(previous.get(model)) if model in previous else 0.0
        if model not in previous or (cur_n - prev_n) > 0:
            diff[model] = val
    return diff


def _model_usage_warning(model_usage_diff: dict, expected: str = "opus") -> str:
    """п.1: «проверь, что в JSON modelUsage только opus» — автоматическая, не разовая проверка, и
    только по РАЗНИЦЕ этого запуска (см. _model_usage_diff), не по кумулятивной истории сессии.
    `expected` — "haiku" для executor: haiku (судья TK-002 п.5в: диспетчер проверяет по разнице
    modelUsage, что запуск реально был на Haiku, не тихо на другой модели)."""
    if not isinstance(model_usage_diff, dict) or not model_usage_diff:
        return None
    bad = [m for m in model_usage_diff if expected not in str(m).lower()]
    if bad:
        return f"modelUsage этого запуска содержит модели без «{expected}» ({', '.join(bad)})"
    return None


def resolve_run_cost(state: dict, result: dict):
    """(стоимость ИМЕННО этого запуска, разница modelUsage, нужна_ли_пометка «как есть» в runs.log).
    Условия судьи TK-002 п.1: --resume отдаёт total_cost_usd/modelUsage КУМУЛЯТИВНО по всей истории
    session_id, не по этому запуску (живой пример 27.09: сессия судьи сходила с $6,80 → $8,29 → $8,74
    кумулятивных; реальные траты запусков — $1,49 и $0,45 — записывались как $8,29 и $8,74, отсюда
    ложная часовая пауза $19,39/ч при реальных ≈ $4,30/ч). (а) разница — по session_id, не по
    хранилищу роли/задачи (переживает ротацию иначе — новый id начинает с нуля сам по себе, так как
    для него нет прошлого итога). (б) нет прошлого итога ИЛИ разница < 0 → берём итог как есть,
    помечаем. Вызывающий должен получить `result` только когда `total_cost_usd` присутствует —
    отсутствие JSON целиком (таймаут/убит) обрабатывается отдельно, см. _finish_run."""
    raw_cost = float(result.get("total_cost_usd"))
    raw_usage = result.get("modelUsage") or {}
    session_id = result.get("session_id")
    if not session_id:
        return raw_cost, raw_usage, True
    seen_costs = state.setdefault("session_cost_seen", {})
    seen_usage = state.setdefault("session_model_usage_seen", {})
    prev_cost = seen_costs.get(session_id)
    prev_usage = seen_usage.get(session_id, {})
    if prev_cost is None or (raw_cost - prev_cost) < 0:
        cost, note = raw_cost, True
    else:
        cost, note = raw_cost - prev_cost, False
    usage_diff = _model_usage_diff(raw_usage, prev_usage)
    seen_costs[session_id] = raw_cost
    seen_usage[session_id] = raw_usage
    return cost, usage_diff, note


def _pid_alive(pid, expect_name: str = None) -> bool:
    """Жив ли pid — и похож ли на наш `claude` (судья 27.09, «можно потом»): подстрочный поиск pid в
    `tasklist` ловил чужие совпадения (123 ⊂ 1234), а pid мог переиспользоваться ОС после перезагрузки
    — точное сравнение PID-колонки через `/FO CSV` + проверка имени образа снижают оба риска (не
    устраняют полностью: другой процесс `claude.exe` с тем же pid теоретически всё ещё возможен)."""
    expect_name = PID_EXPECT_NAME if expect_name is None else expect_name
    if not pid:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                                  capture_output=True, text=True, timeout=5)
            for line in (out.stdout or "").splitlines():
                fields = [f.strip().strip('"') for f in line.split(",")]
                if len(fields) >= 2 and fields[1] == str(pid):
                    return (expect_name or "").lower() in fields[0].lower()
            return False
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except Exception:
        return False
    if not expect_name:
        return True
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as fh:
            return expect_name.lower() in fh.read().lower()
    except OSError:
        return True  # /proc недоступен (не Linux) — не валим проверку живости из-за этого


def _pid_kill(pid) -> None:
    if not pid:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        except Exception:
            pass
        return
    try:
        os.kill(pid, 15)
    except Exception:
        pass


def _proc_alive(info: dict) -> bool:
    if info.get("popen") is not None:
        return info["popen"].poll() is None
    return _pid_alive(info.get("pid"))


def _kill_proc(info: dict) -> None:
    if info.get("popen") is not None:
        try:
            info["popen"].kill()
            info["popen"].wait(timeout=10)
        except Exception:
            pass
    else:
        _pid_kill(info.get("pid"))


# --- запуск роли ------------------------------------------------------------------------------

def _popen(cmd, **kwargs):
    """Точка подмены для тестов (вместо многословного CLAUDE_BIN) — оборачивает subprocess.Popen."""
    return subprocess.Popen(cmd, **kwargs)


def build_prompt(role: str, tid: str, extra_note: str = None) -> str:
    prompt = PROMPT_TEMPLATE.format(role=role, tid=tid, timeout_min=int(RUN_TIMEOUT // 60))
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
    remaining_budget = max(0.0, ticket_budget_usd(state, tid) - ticket_cost_spent(state, tid))
    run_cap = min(remaining_budget, RUN_CAP_USD)
    try:
        launch_tkt = T.read_ticket(ticket_path)
        status_at_launch = launch_tkt.status
        executor = launch_tkt.executor
        log_len_at_launch = len(launch_tkt.log_raw)
    except Exception:
        status_at_launch, executor, log_len_at_launch = None, "", 0
    # executor: haiku (судья TK-002 п.5) — заведомо проверенный на whitelist/обход тикетом (tickets.py
    # new и haiku_refused_reason() в tick()); здесь только сама подмена модели.
    model = CLAUDE_HAIKU_MODEL if executor == "haiku" else CLAUDE_MODEL
    cmd = [CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--permission-mode", "bypassPermissions",
           "--model", model, "--effort", ROLE_EFFORT.get(role, "high"),
           "--max-budget-usd", f"{run_cap:.2f}"]
    if sid:
        cmd += ["--resume", sid]

    env = dict(os.environ)
    # Судья 27.09, п.4 «обязательно»: без этого дочерний claude наследует CLAUDE_CODE_HOST_SESSION_ID
    # сессии CEO (диспетчер сам запущен из неё) — role_context.py/role_memory.py принимают роль за CEO
    # (тревоги/inbox/«молчание» ломаются на все роли). ALPHA_ROLE сама по себе не спасает: find_title()
    # срабатывает раньше при непустом host_id, если сама переменная не снята.
    for _k in list(env):
        if "HOST_SESSION" in _k.upper():
            env.pop(_k, None)
    env["ALPHA_ROLE"] = role

    out_fh = open(run_file, "w", encoding="utf-8")
    err_fh = open(err_file, "w", encoding="utf-8")
    popen = _popen(cmd, cwd=str(PROJECT_ROOT), env=env, stdout=out_fh, stderr=err_fh, text=True)
    RUNNING[tid] = {
        "role": role, "popen": popen, "pid": popen.pid, "started": now, "attempt": attempt,
        "run_file": run_file, "err_file": err_file, "out_fh": out_fh, "err_fh": err_fh, "reason": reason,
        "run_cap_usd": run_cap, "status_at_launch": status_at_launch, "executor": executor,
        "log_len_at_launch": log_len_at_launch,
    }
    # last_woken/log_len_at_launch — для дедупа правила (б) «упоминание» (T.mentions_since — по росту
    # текста секции, не по заголовкам); всегда на (задачу, роль), не зависит от SESSION_SCOPE
    sess_entry = state.setdefault("sessions", {}).setdefault(f"{tid}::{role}", {})
    sess_entry["last_woken"] = T.now_iso(now)
    sess_entry["log_len_at_launch"] = log_len_at_launch
    _record_launch(state, tid, now)
    # зеркало в state.json (pid, задача, роль, старт) — переживает перезапуск диспетчера (recover_active_runs)
    state.setdefault("active_runs", {})[tid] = {
        "role": role, "pid": popen.pid, "started": T.now_iso(now), "attempt": attempt,
        "run_file": str(run_file), "err_file": str(err_file), "reason": reason,
        "run_cap_usd": run_cap, "status_at_launch": status_at_launch, "executor": executor,
        "log_len_at_launch": log_len_at_launch,
    }
    save_state(state)


def _read_run_result(run_file: Path) -> dict:
    try:
        data = Path(run_file).read_text(encoding="utf-8").strip()
        return json.loads(data) if data else {}
    except Exception:
        return {}


def _log_run_summary(tid: str, info: dict, result: dict, now, timed_out: bool, resolved_cost: float,
                      ticket_spent: float, cost_note: bool = False) -> None:
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    usage = result.get("usage") or {}
    status = "timeout" if timed_out else ("ok" if result else "no_output")
    # cost_usd — сырой (кумулятивный за сессию) из JSON; resolved_cost — разница с прошлым итогом ТОЙ ЖЕ
    # session_id (судья TK-002 п.1) — то, что реально начислено этому запуску; cost_note=asis — не было
    # с чем сравнить (новая/ротированная сессия) или разница < 0 — использован сырой итог как есть.
    # ticket_spent — накоплено по ЭТОЙ задаче ПОСЛЕ этого запуска (колонка CEO, роль её не видит).
    line = (f"{T.now_iso(now)} {tid} {info['role']} reason={info.get('reason')} "
            f"attempt={info.get('attempt', 0)} session={result.get('session_id', '-')} "
            f"cost_usd={result.get('total_cost_usd', '-')} resolved_cost={resolved_cost:.4f} "
            f"cost_note={'asis' if cost_note else 'diff'} ticket_spent={ticket_spent:.4f} "
            f"in_tok={usage.get('input_tokens', '-')} out_tok={usage.get('output_tokens', '-')} "
            f"ctx_last={_context_tokens_last(result)} ctx_sum={_context_tokens_sum(usage)} status={status}\n")
    with open(RUNS_LOG, "a", encoding="utf-8") as fh:
        fh.write(line)


def _finish_run(tid: str, info: dict, state: dict, now, timed_out: bool) -> None:
    for fh in (info.get("out_fh"), info.get("err_fh")):
        try:
            fh.close()
        except Exception:
            pass
    state.setdefault("active_runs", {}).pop(tid, None)
    result = _read_run_result(info["run_file"])

    # Стоимость ЭТОГО запуска — разница с прошлым кумулятивным итогом ТОЙ ЖЕ session_id (судья TK-002
    # п.1: --resume отдаёт total_cost_usd/modelUsage кумулятивно за всю историю сессии, не за этот
    # запуск — живой пример 27.09: сессия судьи $6,80 → $8,29 → $8,74 кумулятивных при реальных тратах
    # запусков $1,49 и $0,45, отсюда ложная часовая пауза $19,39/ч). Нет JSON вовсе (убит по таймауту/
    # вручную) — п.1(д): трата теряется НЕДОУЧЁТОМ на этот раз (не досчитываем потолком запуска, как в
    # v1.3 — так считали бы дважды: и потолком сейчас, и разницей на следующем resume той же сессии),
    # суточный/часовой итог в этом случае не точен — известное ограничение, не пытаемся угадать число.
    if result.get("total_cost_usd") is not None:
        resolved_cost, model_usage_diff, cost_note = resolve_run_cost(state, result)
    else:
        resolved_cost, model_usage_diff, cost_note = 0.0, {}, True
    _add_cost(state, now, resolved_cost)
    add_ticket_cost(state, tid, resolved_cost)
    _record_cost_event(state, now, resolved_cost)
    _log_run_summary(tid, info, result, now, timed_out, resolved_cost, ticket_cost_spent(state, tid), cost_note)

    expected_model = "haiku" if info.get("executor") == "haiku" else "opus"
    model_warn = _model_usage_warning(model_usage_diff, expected_model)
    if model_warn:
        route_ceo_signal(tid, "model", model_warn, state, now)

    role = info["role"]
    key = f"{tid}::{role}"
    sess = state.setdefault("sessions", {}).setdefault(key, {})  # retries — всегда на (задачу, роль)
    store = _resume_store(state, tid, role)  # session_id/токены — по SESSION_SCOPE[role]
    if result.get("session_id"):
        store["session_id"] = result["session_id"]
    store["last_context_tokens"] = _context_tokens_last(result)  # контекст последнего хода, не сумма по ходам

    path = TICKETS_DIR / f"{tid}.md"
    if not path.exists():
        save_state(state)
        return
    tkt = T.read_ticket(path)
    # Судья 27.09, п.7 «обязательно»: таймаут сам по себе — не провал, если роль успела записать
    # прогресс до убийства процесса (RUN_TIMEOUT назван в промпте — роль знает лимит шага). Раньше
    # `logged` форсировалось в False при timed_out=True независимо от факта записи.
    # CEO 27.09, TK-005: «есть запись» — это РОСТ секции «## Лог» (длина текста после launch), не
    # наличие заголовка `### <ISO> <автор>` — роли пишут по-разному (без заголовка, TK-005 ложный
    # blocked дважды подряд, b0b63cb/e657090). Секция — «допиши», не «перепиши»: рост length — точный
    # признак записи независимо от формата строки.
    logged = len(tkt.log_raw) > info.get("log_len_at_launch", 0)
    stuck_todo = logged and tkt.status == "todo"  # роль отчиталась, но забыла увести статус с todo
    status_changed = tkt.status != info.get("status_at_launch")

    # Холостой ход (п.5, владелец 27.09): запуск стоил дороже половины бюджета задачи и не оставил ни
    # записи, ни смены статуса — сразу blocked, без обычного одного повтора (повтор может сжечь
    # ещё половину бюджета так же безрезультатно).
    budget = ticket_budget_usd(state, tid)
    if (not logged) and (not status_changed) and budget > 0 and resolved_cost > 0.5 * budget:
        T.write_header_updates(path, {"status": "blocked"}, now=now)
        T.append_log(path, "dispatcher",
                     f"Запуск роли {role} стоил ${resolved_cost:.2f} (> половины бюджета задачи) и не "
                     "оставил ни записи, ни смены статуса — холостой ход, задача заблокирована, нужен @ceo.",
                     now=now)
        sess["retries"] = 0
        append_ceo_inbox(tid, "blocked", f"{role}: холостой ход, ${resolved_cost:.2f} без результата", now)
        save_state(state)
        return

    if logged and not stuck_todo:
        sess["retries"] = 0
        save_state(state)
        return

    # (д) запуск завершился без пригодного результата — один повтор, затем blocked
    if info.get("attempt", 0) < 1:
        if not logged:
            note = ("Предыдущий запуск не оставил новую запись в «## Лог» — обязательно допиши итог и "
                    "обнови status." if not timed_out else
                    "Предыдущий запуск не уложился в таймаут — сократи шаг и обязательно запиши итог.")
        else:
            note = ("Запись в «## Лог» есть, но status остался todo — обязательно смени статус (например "
                    "in_progress/waiting/done), иначе задача возьмётся в работу заново.")
        if _daily_budget_exceeded(state, now):
            _notify_budget_once(state, now)
            append_ceo_inbox(tid, "budget", "повтор отложен — суточный потолок стоимости достигнут", now)
            save_state(state)
            return
        if _hour_budget_exceeded(state, now):
            append_ceo_inbox(tid, "hour-budget", "повтор отложен — часовая скорость трат исчерпана", now)
            save_state(state)
            return
        if ticket_budget_exceeded(state, tid):
            notify_ticket_budget_exceeded(path, tid, state, now)
            save_state(state)
            return
        launch_run(path, role, state, now, reason="retry", attempt=info.get("attempt", 0) + 1, extra_note=note)
        sess["retries"] = sess.get("retries", 0) + 1
    else:
        why = ("статус остался todo дважды подряд" if stuck_todo else
               "дважды не уложился в таймаут" if timed_out else
               "дважды не оставил запись в «## Лог»")
        T.write_header_updates(path, {"status": "blocked"}, now=now)
        # роль без "@" намеренно (судья 27.09, п.1 «обязательно», защита №2 сверх исключения
        # author=="dispatcher" в decide(): своя запись не должна выглядеть упоминанием роли)
        T.append_log(path, "dispatcher",
                     f"Запуск роли {role} — {why} — задача заблокирована, нужен @ceo.", now=now)
        sess["retries"] = 0
        append_ceo_inbox(tid, "blocked", f"{role}: {why}", now)
    save_state(state)


def _poll_running(state: dict, now) -> None:
    for tid in list(RUNNING):
        info = RUNNING[tid]
        if _proc_alive(info):
            if (now - info["started"]).total_seconds() > RUN_TIMEOUT:
                _kill_proc(info)
                del RUNNING[tid]
                _finish_run(tid, info, state, now, timed_out=True)
            continue
        del RUNNING[tid]
        _finish_run(tid, info, state, now, timed_out=False)


def recover_active_runs(state: dict, now) -> None:
    """После перезапуска диспетчера — подхватить зеркало state.json["active_runs"]: живой pid не
    запускаем повторно (просто продолжаем отслеживать по pid), уже закончившийся — обрабатываем как
    обычное завершение прогона (лог/ретрай/blocked), раз диспетчер это пропустил, пока не работал."""
    for tid, saved in list(state.get("active_runs", {}).items()):
        if tid in RUNNING:
            continue  # уже отслеживаем в этом процессе (это не перезапуск)
        info = {
            "role": saved.get("role"), "popen": None, "pid": saved.get("pid"),
            "started": T.parse_dt(saved["started"]), "attempt": saved.get("attempt", 0),
            "run_file": Path(saved["run_file"]), "err_file": Path(saved.get("err_file") or ""),
            "out_fh": None, "err_fh": None, "reason": saved.get("reason", "recovered"),
            "run_cap_usd": saved.get("run_cap_usd", 0.0), "status_at_launch": saved.get("status_at_launch"),
            "executor": saved.get("executor", ""), "log_len_at_launch": saved.get("log_len_at_launch", 0),
        }
        if _pid_alive(saved.get("pid")):
            RUNNING[tid] = info
        else:
            state.get("active_runs", {}).pop(tid, None)
            _finish_run(tid, info, state, now, timed_out=False)


# --- тик / цикл -------------------------------------------------------------------------------

def tick(now=None) -> int:
    now = now or datetime.now().astimezone()
    state = load_state()
    recover_active_runs(state, now)  # диспетчер мог перезапуститься — живые/умершие прогоны из state.json
    _poll_running(state, now)
    save_state(state)

    budget_exceeded = _daily_budget_exceeded(state, now)
    if budget_exceeded:
        _notify_budget_once(state, now)
    hour_exceeded = _notify_hour_budget(state, now)  # скорость трат по ВСЕМ ролям — п.4

    launched = 0
    for path in T.list_tickets(TICKETS_DIR):
        try:
            tkt = T.read_ticket(path)
        except Exception as e:
            notify_parse_error(path.stem, f"{type(e).__name__}: {e}", state, now)
            continue

        handle_ceo_mentions(tkt, state, now)
        notify_status_for_ceo(tkt, state, now)
        notify_done_without_reviewer(tkt, state, now)
        if tkt.status == "done":
            notify_budget_proportionality(tkt.id, state, now)

        tid = tkt.id
        ticket_over_budget = ticket_budget_exceeded(state, tid)
        if ticket_over_budget:
            notify_ticket_budget_exceeded(path, tid, state, now)  # п.2: needs_owner + строка CEO

        if tid in RUNNING or len(RUNNING) >= MAX_PARALLEL:
            continue
        decision = decide(tkt, state, now)
        if decision is None:
            continue
        haiku_reason = haiku_refused_reason(tkt)
        if haiku_reason:
            T.write_header_updates(path, {"status": "blocked"}, now=now)
            T.append_log(path, "dispatcher", f"{haiku_reason} — задача заблокирована, нужен @ceo.", now=now)
            route_ceo_signal(tid, "blocked", haiku_reason, state, now)
            continue
        if budget_exceeded or hour_exceeded or ticket_over_budget:
            continue  # суточный/часовой потолок или бюджет задачи — новые запуски не стартуют
        if _role_busy(decision.role):
            continue  # SESSION_SCOPE="role": у роли уже идёт другая задача — своей очереди ждём
        if _rate_limited(state, tid, now):
            continue  # MAX_RUNS_PER_TICKET_HOUR/MIN_GAP_S — пауза, не ошибка; попробуем следующим тиком
        if decision.header_updates:
            T.write_header_updates(path, decision.header_updates, now=now)
        launch_run(path, decision.role, state, now, reason=decision.reason)
        launched += 1

    state["last_tick"] = T.now_iso(now)  # судья TK-002 п.2а: сторож проверяет диспетчер жив по этому
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
