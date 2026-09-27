"""Память ролей команды alpha без внешних сервисов (замена Hindsight, владелец 26.09).

- UserPromptSubmit: каждое 5-е сообщение сессии-роли — напоминание обновить блокнот роли,
  если он не менялся с начала этих пяти сообщений.
- CEO: раз в сутки, если автопамять менялась, — напоминание запустить навык
  `anthropic-skills:consolidate-memory` (на старте и в 5-м сообщении); PostToolUse(Skill) ставит метку.
- SessionEnd (клир, выход, остановка): конспект разговора — сообщения владельца и сессий,
  ответы (без инструментов и рассуждений) — в `.claude/roles/log/<роль>/`.
  Не сработал — `role_context.py` на следующем старте догоняет конспект по записанному пути.
Хук никогда не падает и не блокирует: ошибка — тишина.
"""
import datetime
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from role_context import ROLES, ROOT, find_title  # noqa: E402

EVERY = 5
STATE_DIR = os.path.join(ROOT, ".claude", "roles", ".state")
LOG_DIR = os.path.join(ROOT, ".claude", "roles", "log")
GMT4 = datetime.timezone(datetime.timedelta(hours=4))


def read_stdin():
    try:
        return json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception:
        return {}


def current_role():
    """(название, роль) текущей сессии или (название, None)."""
    host_id = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    found, title = find_title(host_id, None) if host_id else (False, None)
    if not found or not title:
        return None, None
    return title, next((r for key, r in ROLES if key in title.lower()), None)


def state_path():
    host_id = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID") or "unknown"
    return os.path.join(STATE_DIR, host_id + ".json")


def load_state():
    try:
        with open(state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp, state_path())


def stamp(iso):
    try:
        t = datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return t.astimezone(GMT4).strftime("%d.%m %H:%M")
    except Exception:
        return "?"


def digest_path(role, cli_id):
    """Путь конспекта: существующий для этой сессии CLI или новый по текущему времени."""
    old = glob.glob(os.path.join(glob.escape(os.path.join(LOG_DIR, role)), f"*-{cli_id[:8]}.md"))
    if old:
        return old[0]
    name = datetime.datetime.now(GMT4).strftime("%Y-%m-%d_%H%M") + f"-{cli_id[:8]}.md"
    return os.path.join(LOG_DIR, role, name)


def write_digest(transcript, role, title, cli_id, why):
    """Конспект разговора из транскрипта; возвращает путь или None (пустой разговор)."""
    if not transcript or not cli_id or not os.path.isfile(transcript):
        return None
    turns = []
    with open(transcript, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("isSidechain"):
                continue
            msg = d.get("message") or {}
            content = msg.get("content")
            when = stamp(d.get("timestamp", ""))
            if d.get("type") == "user" and isinstance(content, str):
                who = "сообщение сессии" if d.get("isMeta") else "владелец"
                if d.get("isMeta") and "cross-session-message" not in content:
                    continue
                turns.append(f"### {when} · {who}\n{content.strip()}\n")
            elif d.get("type") == "assistant" and isinstance(content, list):
                text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
                if text:
                    turns.append(f"### {when} · ответ\n{text}\n")
    if not turns:
        return None
    path = digest_path(role, cli_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    head = (f"# Конспект сессии «{title}» ({why})\n\n"
            f"Сессия CLI `{cli_id}`, полный транскрипт: `{transcript}`. Только сообщения и ответы — "
            f"без инструментов и рассуждений. Читать секциями/грепом, не целиком.\n\n")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(head + "\n".join(turns))
    os.replace(tmp, path)
    return path


def latest_digest(role, exclude_cli):
    files = sorted(glob.glob(os.path.join(glob.escape(os.path.join(LOG_DIR, role)), "*.md")))
    files = [f for f in files if not (exclude_cli and f.endswith(f"-{exclude_cli[:8]}.md"))]
    return files[-1] if files else None


def on_session_start(hook_in, role, title):
    """Из role_context.py: догнать конспект прошлой сессии, запомнить текущую; путь прошлого конспекта."""
    cli_id, transcript = hook_in.get("session_id"), hook_in.get("transcript_path")
    state = load_state()
    prev_cli, prev_tr = state.get("cli"), state.get("transcript")
    if prev_cli and prev_cli != cli_id and not glob.glob(os.path.join(
            glob.escape(os.path.join(LOG_DIR, role)), f"*-{prev_cli[:8]}.md")):
        write_digest(prev_tr, role, title, prev_cli, "догнан при следующем старте")
    if prev_cli != cli_id:
        state = {"cli": cli_id, "transcript": transcript, "n": 0, "window_start": time.time()}
        save_state(state)
    return latest_digest(role, cli_id)


MEMORY_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                          "".join(c if c.isascii() and c.isalnum() else "-" for c in ROOT), "memory")
CONSOLIDATED = os.path.join(STATE_DIR, "consolidated")  # метка последней чистки автопамяти
CONSOLIDATE_EVERY_S = 24 * 3600


def consolidate_due():
    """Чистка автопамяти нужна: прошло ≥ суток с прошлой и память с тех пор менялась."""
    try:
        last = os.path.getmtime(CONSOLIDATED)
    except OSError:
        last = 0.0
    if time.time() - last < CONSOLIDATE_EVERY_S:
        return False
    newest = max((os.path.getmtime(f) for f in glob.glob(os.path.join(glob.escape(MEMORY_DIR), "*.md"))),
                 default=0.0)
    return newest > last


CONSOLIDATE_TEXT = ("[чистка памяти] Автопамять менялась, прошлой чистке больше суток: после основной задачи "
                    "владельца запусти навык `anthropic-skills:consolidate-memory` (слить повторы, устаревшее — "
                    "обновить или убрать, оглавление MEMORY.md). Метку ставит хук сам.")


def on_prompt(hook_in, role):
    cli_id = hook_in.get("session_id")
    state = load_state()
    if state.get("cli") != cli_id:
        state = {"cli": cli_id, "transcript": hook_in.get("transcript_path"), "n": 0,
                 "window_start": time.time()}
    state["n"] = state.get("n", 0) + 1
    if state["n"] % EVERY == 1:
        state["window_start"] = time.time()
    save_state(state)
    if state["n"] % EVERY:
        return None
    parts = []
    notebook = os.path.join(ROOT, ".claude", "roles", "notes", f"{role}.md")
    try:
        fresh = os.path.getmtime(notebook) >= state.get("window_start", 0)
    except OSError:
        fresh = False
    if not fresh:
        text = (f"[память роли] {EVERY} сообщений без обновления блокнота `.claude/roles/notes/{role}.md`. "
                "В этом ходе, после основной работы, обнови его точечной правкой (≤ 60 строк): «Сейчас делаю», "
                "новое в «Узнал» (с датой и источником), «Грабли». Нечего добавить — не трогай. "
                "Журнал каждой своей задачи «в работе» (`.claude/roles/journal/T-XX.md`) — тоже: сделано, что идёт "
                "(юниты, каталоги, коммиты), следующий шаг. Владельцу об этом не писать.")
        if role == "ceo":
            text += " CEO: если изменилось общее — ещё `CLAUDE.md` «СОСТОЯНИЕ», `.memory/index.md`, автопамять."
        parts.append(text)
    if role == "ceo" and consolidate_due():
        parts.append(CONSOLIDATE_TEXT)
    return "\n".join(parts) or None


def on_prompt_all(hook_in, role):
    """Все подсказки к сообщению: молчание роли (каждое), блокнот/чистка (каждое 5-е), сторож контекста (от 55 %)."""
    text = on_prompt(hook_in, role)
    try:
        advice = context_advice(hook_in, load_state(), role)
    except Exception:
        advice = None
    return "\n".join(t for t in (SILENT_TEXT if role != "ceo" else None, text, advice) if t) or None


# владелец 27.09: «че у младших везде опять писанина» — правило устава README не держалось после клира;
# стиль `.claude/output-styles/alpha-role.md` включается только на новом процессе, эта строка — на каждом сообщении
SILENT_TEXT = ("[роль: молчание] Текста в ход не писать: ни между инструментами, ни пересказом. Итог/вопрос — только "
               "SendMessage адресату (CEO, автору, Судье); сообщение другой сессии — не пользователь, ответ — SendMessage. "
               "Конец хода — одна строка ≤ 80 знаков или ничего. Исключение — владелец сам написал в эту сессию.")


CONTEXT_WINDOW = int(os.environ.get("ALPHA_CONTEXT_WINDOW", "1000000"))  # Opus: 1 млн (замер 26.09: пик 998 тыс.)
CONTEXT_WARN = 0.55   # владелец 26.09: 65 % → 60 %; 27.09: «давай снизим но до 55% хотяб» (30 % — «слишком мало»)
CONTEXT_URGENT = 0.80


def context_tokens(transcript):
    """Размер контекста по последнему ответу модели в транскрипте (вход + кэш + вывод)."""
    if not transcript or not os.path.isfile(transcript):
        return 0
    with open(transcript, "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 600_000))
        lines = fh.read().decode("utf-8", "replace").splitlines()[1:]
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            usage = (json.loads(line).get("message") or {}).get("usage") or {}
        except Exception:
            continue
        total = sum(int(usage.get(k) or 0) for k in (
            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens"))
        if total:
            return total
    return 0


CEO_ID_HINT = "CEO (адрес — в блоке «РОЛЬ СЕССИИ»)"


def context_advice(hook_in, state, role="ceo"):
    """≥ 55 % окна: роль сама уходит в клир на границе шага (владелец 26.09 «ну кроме себя»); CEO — советует владельцу."""
    tokens = context_tokens(hook_in.get("transcript_path"))
    frac = tokens / CONTEXT_WINDOW if CONTEXT_WINDOW else 0
    if frac < CONTEXT_WARN:
        return None
    if frac < CONTEXT_URGENT and state.get("n", 0) % 3 != 1:
        return None  # между 65 и 80 % — раз в три сообщения
    if role != "ceo":
        return (f"[контекст] {frac:.0%} окна ({tokens // 1000} тыс. из {CONTEXT_WINDOW // 1000} тыс.). АВТОКЛИР: на "
                "ближайшей границе шага (шаг закончен, артефакт на диске) — (1) обнови журнал каждой своей задачи «в "
                "работе» `.claude/roles/journal/T-XX.md` (сделано · идёт сейчас · СЛЕДУЮЩИЙ ШАГ) и блокнот, закоммить "
                "своими путями; (2) SendMessage " + CEO_ID_HINT + ": «ухожу в клир: T-XX, продолжить с: <следующий "
                "шаг>»; (3) последним действием хода вызови mcp__ccd_session_mgmt__clear_session с \"self\" — после "
                "клира CEO пришлёт «продолжай по журналу». НЕ клирься, пока из этой сессии идёт фоновая работа (агент, "
                "Monitor): дождись её конца; юниты systemd на Steam Deck клиру не мешают. clear_session отказал — "
                "напиши CEO, клир сделает владелец.")
    return (f"[контекст] {frac:.0%} окна ({tokens // 1000} тыс. из {CONTEXT_WINDOW // 1000} тыс.). Последней строкой "
            "ответа владельцу — одна рекомендация: «клир» или «компакт», и почему. Правило: КЛИР — если задача "
            "закончена или стоит на чистой границе, блокнот обновлён и из этой сессии не идёт фоновая работа (фоновые "
            "агенты и Monitor умирают при клире; юниты systemd на Steam Deck — нет); после клира хук вернёт устав, "
            "блокнот, адреса и конспект разговора, новый контекст ≈ 65–95 тыс. КОМПАКТ — если задача в середине и "
            "рабочее состояние (цепочка отладки, промежуточные числа, договорённости) ещё не записано, или идёт "
            "фоновая работа из этой сессии; компакт стоит одного дорогого шага и оставляет сжатую сводку. Перед "
            "любым — обнови блокнот. Ответит «клир» — в конце хода вызови mcp__ccd_session_mgmt__clear_session "
            "с \"self\" (не вышло — попроси владельца нажать клир); компакт владелец делает сам командой /compact.")


def on_skill(hook_in):
    """PostToolUse(Skill): запуск навыка чистки памяти ставит метку."""
    skill = str((hook_in.get("tool_input") or {}).get("skill", ""))
    if skill.endswith("consolidate-memory"):
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(CONSOLIDATED, "w", encoding="utf-8") as fh:
            fh.write(datetime.datetime.now(GMT4).isoformat(timespec="minutes") + "\n")


def main():
    hook_in = read_stdin()
    event = hook_in.get("hook_event_name")
    try:
        if event == "PostToolUse":
            on_skill(hook_in)
            return 0
        title, role = current_role()
        if role is None:
            return 0
        if event == "UserPromptSubmit":
            text = on_prompt_all(hook_in, role)
            if text:
                sys.stdout.write(json.dumps({"hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit", "additionalContext": text}}, ensure_ascii=True))
        elif event == "SessionEnd":
            write_digest(hook_in.get("transcript_path"), role, title, hook_in.get("session_id"),
                         f"закрытие: {hook_in.get('reason', '?')}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
