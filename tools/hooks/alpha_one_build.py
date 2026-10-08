"""PreToolUse (Bash/PowerShell) для проекта alpha: сборки cargo на машине владельца запрещены.

Сборки cargo (build/test/clippy/check/run/bench/doc/rustc/nextest) проекта alpha на машине владельца запрещены
(владелец 28.09: «зачем на моей машине») — только VPS: `bash tools/vps-check.sh <дерево> all`. Удалённые сборки
(ssh …) и `cargo fmt` не трогаются.

Страж удаления здесь НЕ вызывается: он — `delete_guard.py` плагина role-play-vibing и подключён его собственным
`hooks/hooks.json` (TK-090 Д-4: раньше хук владельца вызывал его второй раз). Этот файл — копия рабочего хука
`~/.claude/hooks/alpha_one_build.py`; правка — здесь, затем `cp tools/hooks/alpha_one_build.py ~/.claude/hooks/`.
Подключение: ~/.claude/settings.json → hooks.PreToolUse, матчер `Bash|PowerShell`; в команде после `python …` стоит
`|| … exit 2` для сессий в alpha — интерпретатор не найден или хук упал до своего кода = блокирующая ошибка.
"""
import json
import os
import re
import sys

SHELL_TOOLS = ("Bash", "PowerShell")

# cargo в позиции команды (начало строки, после ; & | ( или then/do, либо путь к cargo.exe) — чтобы текст о cargo в
# heredoc/строках правок не ловился (ложные отказы 28.09)
BUILD = re.compile(r"(?:^|[;&|(]\s*|\b(?:then|do|time|exec)\s+|[\\/])cargo(?:\.exe)?\"?\s+(?:\+\S+\s+)?"
                   r"(?:build|test|clippy|check|run|bench|doc|rustc|nextest)\b", re.M)
REASON_LOCAL = ("Сборки и тесты cargo проекта alpha на машине владельца запрещены (владелец 28.09: «зачем на моей машине»). "
                "Только VPS: bash \"C:/visual projects/alpha/tools/vps-check.sh\" <рабочее дерево> [test|clippy|fmt|build|all] [фильтр]; "
                "бинарник для Steam Deck — сборкой на VPS (docs/COMMANDS.md).")
REASON_DOWN = ("Хук запрета сборок alpha не работает ({e}) — отказ по умолчанию (fail-closed). "
               "Хук: ~/.claude/hooks/alpha_one_build.py (копия — tools/hooks/); не получилось — сообщите CEO.")


def in_alpha(text):
    t = str(text or "")
    return "visual projects" in t.replace("\\", "/").lower() and "alpha" in t.lower()


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}},
        ensure_ascii=True))


def main():
    if os.environ.get("ALPHA_HOOK_SENTINEL"):  # проверка, что хук вызывается
        with open(os.environ["ALPHA_HOOK_SENTINEL"], "a", encoding="utf-8") as fh:
            fh.write("fired\n")
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise ValueError("событие не объект JSON")
    except Exception as e:
        if in_alpha(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()):
            deny(REASON_DOWN.format(e=f"{type(e).__name__}: {e}"[:120]))
        return
    if data.get("tool_name") not in SHELL_TOOLS:
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not BUILD.search(cmd) or re.search(r"ssh", cmd):
        return
    if "alpha" not in (str(data.get("cwd") or "") + " " + cmd).lower():
        return
    deny(REASON_LOCAL)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:      # любой сбой самого хука в alpha — отказ, не пропуск
        if in_alpha(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()):
            deny(REASON_DOWN.format(e=f"{type(e).__name__}: {e}"[:200]))
