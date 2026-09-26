"""SessionStart: возвращает сессии команды alpha её роль — при старте, клире, сжатии, возобновлении.

Роль = название сессии в Claude Desktop (устав `.claude/roles/README.md`). Название берётся из
метаданных приложения по `CLAUDE_CODE_HOST_SESSION_ID` (оно переживает клир); в контекст
вставляются устав команды, устав роли и блокнот роли. Хук никогда не падает: не вышло определить
роль — вставляет инструкцию определить её вручную запросом `get_session("self")`.
"""
import glob
import json
import os
import sys

# (подстрока названия в нижнем регистре, файл устава) — первое совпадение
ROLES = [
    ("исследователь", "researcher"),
    ("инженер", "engineer"),
    ("судья", "judge"),
    ("ceo", "ceo"),
]
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROLES_DIR = os.path.join(ROOT, ".claude", "roles")

MANUAL = (
    "Если эта сессия — участник команды alpha, до любых действий вызови "
    "mcp__ccd_session_mgmt__get_session с session_id \"self\" и по полю title прочитай: "
    ".claude/roles/README.md, устав роли (`CEO` → ceo.md, `Роль: Исследователь` → researcher.md, "
    "`Роль: Инженер` → engineer.md, `Роль: Судья` → judge.md) и блокнот .claude/roles/notes/<роль>.md. "
    "Название не из списка — ты не в команде: см. правило ниже."
)
OUTSIDER = (
    "Общую память проекта (CLAUDE.md «СОСТОЯНИЕ», .memory/, docs/plan/SETTLED.md, автопамять "
    "~/.claude/projects/…/memory/) пишет только сессия `CEO`; задачи из .claude/roles/TASKS.md "
    "без просьбы владельца не брать."
)


def session_dirs():
    appdata = os.environ.get("APPDATA")
    if appdata:
        yield os.path.join(appdata, "Claude", "claude-code-sessions")
    local = os.environ.get("LOCALAPPDATA")
    if local:  # установка из Microsoft Store: данные приложения в пакете
        yield from glob.glob(os.path.join(
            glob.escape(local), "Packages", "Claude_*", "LocalCache", "Roaming", "Claude",
            "claude-code-sessions"))


def find_title(host_id, cli_id):
    """Название сессии: по id сессии приложения, иначе по id сессии CLI."""
    for d in session_dirs():
        if not os.path.isdir(d):
            continue
        pattern = os.path.join(glob.escape(d), "*", "*", "local_*.json")
        for f in glob.glob(pattern):
            name = os.path.basename(f)[: -len(".json")]
            if host_id and name != host_id:
                continue
            with open(f, encoding="utf-8") as fh:
                meta = json.load(fh)
            if host_id or (cli_id and meta.get("cliSessionId") == cli_id):
                return True, meta.get("title")
    return False, None


def team_addresses():
    """«название → id» сессий-ролей этой папки: по id сообщение доходит и до остановленной сессии."""
    rows = {}
    for d in session_dirs():
        for f in glob.glob(os.path.join(glob.escape(d), "*", "*", "local_*.json")):
            try:
                with open(f, encoding="utf-8") as fh:
                    meta = json.load(fh)
            except Exception:
                continue
            title = meta.get("title") or ""
            if meta.get("isArchived") or os.path.normcase(meta.get("cwd") or "") != os.path.normcase(ROOT):
                continue
            if any(key in title.lower() for key, _ in ROLES):
                rows[title] = meta.get("sessionId")
    return "; ".join(f"`{t}` → `{i}`" for t, i in sorted(rows.items()))


ROLE_NAMES = {"researcher": "Исследователь", "engineer": "Инженер", "judge": "Судья", "ceo": "CEO"}
JOURNAL_DIR = os.path.join(ROLES_DIR, "journal")


def role_rows(role, status):
    """Строки `TASKS.md` роли с этим статусом: [(ид, задача, зависит от)]."""
    name = ROLE_NAMES.get(role, "")
    rows = []
    try:
        with open(os.path.join(ROLES_DIR, "TASKS.md"), encoding="utf-8") as fh:
            for line in fh:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) >= 5 and cells[0].startswith("T-") and name in cells[2] and cells[4].startswith(status):
                    rows.append((cells[0], cells[1], cells[3]))
    except OSError:
        pass
    return rows


def active_tasks(role):
    """Строки `TASKS.md` в работе у роли: [(ид, текст задачи)]."""
    return [(tid, text) for tid, text, _ in role_rows(role, "в работе")]


def queue(role):
    """Очередь роли («ждёт»): что брать, пока задача в работе ждёт чужого сигнала."""
    rows = role_rows(role, "ждёт")
    if not rows:
        return None
    lines = [f"- {tid} (зависит от: {dep or '—'}): {text[:160]}" for tid, text, dep in rows]
    return ("--- очередь роли в TASKS.md («ждёт») — пока задача в работе ждёт чужого сигнала, бери отсюда первую без "
            "блокирующей зависимости; простаивать при непустой очереди — брак ---\n" + "\n".join(lines))


def journals(role):
    """Журналы задач роли в работе — чтобы после клира/сжатия продолжить с того же места."""
    out = []
    for tid, text in active_tasks(role):
        path = os.path.join(JOURNAL_DIR, f"{tid}.md")
        head = f"--- журнал {tid}: {text[:240]} ---"
        try:
            with open(path, encoding="utf-8") as fh:
                lines = fh.read().strip().splitlines()
            out.append(head + "\n" + "\n".join(lines[-80:]))
        except OSError:
            out.append(head + f"\nЖУРНАЛА НЕТ — заведи `.claude/roles/journal/{tid}.md` (формат — README «Журнал задачи»).")
    return out


def read(rel):
    path = os.path.join(ROLES_DIR, rel)
    with open(path, encoding="utf-8") as fh:
        return f"--- .claude/roles/{rel.replace(os.sep, '/')} ---\n{fh.read().strip()}\n"


def context(source):
    try:
        hook_in = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
    except Exception:
        hook_in = {}
    source = hook_in.get("source") or source
    host_id = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID")
    try:
        found, title = find_title(host_id, hook_in.get("session_id"))
    except Exception as e:
        return f"=== РОЛЬ СЕССИИ: не определена ({type(e).__name__}: {e}) ===\n{MANUAL}\n{OUTSIDER}"
    if not found or not title:
        why = "метаданные сессии не найдены" if not found else "у сессии нет названия"
        return f"=== РОЛЬ СЕССИИ: не определена ({why}) ===\n{MANUAL}\n{OUTSIDER}"
    role = next((r for key, r in ROLES if key in title.lower()), None)
    if role is None:
        return f"=== Сессия «{title}» — не роль команды alpha ===\n{OUTSIDER}"
    try:
        parts = [read("README.md"), read(f"{role}.md"), read(os.path.join("notes", f"{role}.md"))]
    except Exception as e:
        return (f"=== РОЛЬ СЕССИИ: «{title}», но устав не прочитан ({type(e).__name__}: {e}) ===\n"
                f"{MANUAL}")
    head = [
        f"=== РОЛЬ СЕССИИ: ты — «{title}» команды alpha (вставлено хуком "
        f".claude/hooks/role_context.py, событие: {source}) ===",
        "Устав команды, устав твоей роли и твой блокнот — ниже, перечитывать не нужно. "
        "Общий список задач — .claude/roles/TASKS.md.",
    ]
    try:
        addresses = team_addresses()
        if addresses:
            head.append("Адреса команды для SendMessage (по id доходит и до остановленной сессии, которой нет в "
                        f"ListAgents): {addresses}.")
    except Exception:
        pass
    try:  # память ролей: догнать конспект прошлой сессии, дать на него ссылку
        from role_memory import CONSOLIDATE_TEXT, consolidate_due, on_session_start
        last = on_session_start(hook_in, role, title)
        if role == "ceo" and consolidate_due():
            head.append(CONSOLIDATE_TEXT)
    except Exception:
        last = None
    if last:
        head.append(f"Конспект прошлой сессии этой роли (до клира/перезапуска): `{os.path.relpath(last, ROOT).replace(os.sep, '/')}` — "
                    "не читать целиком; грепом/секциями, если блокнота не хватает.")
    if role != "ceo":
        head.append("«Первое в новом чате» в CLAUDE.md — очередь CEO, не твоя задача: действуй только "
                    "по строке TASKS.md своей зоны, сообщению CEO или владельца. " + OUTSIDER)
    try:
        parts.extend(journals(role))
        q = queue(role)
        if q:
            parts.append(q)
    except Exception:
        pass
    return "\n".join(head) + "\n\n" + "\n".join(parts)


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else "?"
    try:
        text = context(source)
    except Exception as e:  # хук не должен ломать старт сессии
        text = f"=== РОЛЬ СЕССИИ: сбой хука ({type(e).__name__}: {e}) ===\n{MANUAL}\n{OUTSIDER}"
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}
    sys.stdout.write(json.dumps(out, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
