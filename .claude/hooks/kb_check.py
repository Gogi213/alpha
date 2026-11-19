#!/usr/bin/env python3
"""PreToolUse (Bash/PowerShell): `git commit` при правках в kb/ → `tools/kb.py precommit --changed` (TK-629).

Красная проверка карточек (битые ссылки, сироты, дубли, front matter) — отказ (код 2) с выводом `kb check`; правок в kb/ нет — тихо пропускает.
Тот же вызов ставит в `.git/hooks/pre-commit` команда `python tools/kb.py install-hook`."""
import json, os, re, subprocess, sys

COMMIT = re.compile(r"\bgit\b[^;&|\n]*?\bcommit\b")


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return 0
    if d.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    if not COMMIT.search((d.get("tool_input") or {}).get("command") or ""):
        return 0
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    script = os.path.join(root, "tools", "kb.py")
    if not os.path.exists(script):
        return 0
    try:
        r = subprocess.run([sys.executable, script, "precommit", "--changed"], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=30, env=dict(os.environ, PYTHONUTF8="1"))
    except Exception:
        return 0
    if r.returncode == 1:
        print("kb check красный — коммит остановлен, исправь карточки:\n" + r.stdout.decode("utf-8", "replace")[-3000:], file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
