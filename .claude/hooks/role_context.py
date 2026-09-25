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


def read(rel):
    path = os.path.join(ROLES_DIR, rel)
    with open(path, encoding="utf-8") as fh:
        return f"--- .claude/roles/{rel.replace(os.sep, '/')} ---\n{fh.read().strip()}\n"


def context(source):
    try:
        hook_in = json.load(sys.stdin)
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
    if role != "ceo":
        head.append("«Первое в новом чате» в CLAUDE.md — очередь CEO, не твоя задача: действуй только "
                    "по строке TASKS.md своей зоны, сообщению CEO или владельца. " + OUTSIDER)
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
