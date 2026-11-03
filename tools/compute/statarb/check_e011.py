#!/usr/bin/env python3
"""E-011: проверка E-010 (check_e010.py) не зависит от того, что собиралось в слоте calc перед ней.
Подряд: чистое дерево 75d318ab (проверка обязана быть красной: rc 101, упал тест) → чистое дерево HEAD (зелёной)
→ 75d318ab → HEAD. rc 0 — все четыре шага как ожидалось. Красная до правки E-011: после сборки 75d318ab слот отдаёт
её бинарник дереву HEAD (шаг «после» rc 101) или check_e010.py не берёт дерево-аргумент (шаг «до» зелёный).
Четыре сборки на calc — timeout записи E-011 ставить с запасом."""
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
CHECK = os.path.join(REPO, "tools", "compute", "statarb", "check_e010.py")
BEFORE = "75d318ab"


def run(tree: str) -> int:
    return subprocess.run([sys.executable, CHECK, tree], cwd=tree).returncode


def main() -> int:
    base = tempfile.mkdtemp(prefix="e011-")
    old, new = os.path.join(base, "before"), os.path.join(base, "after")
    bad = []
    try:
        for tree, rev in ((old, BEFORE), (new, "HEAD")):
            r = subprocess.run(["git", "-C", REPO, "worktree", "add", "--detach", tree, rev], capture_output=True, text=True)
            if r.returncode:
                print(f"e011: git worktree add {rev}: {r.stderr.strip()}", file=sys.stderr)
                return 2
        for i, (name, tree, want_rc) in enumerate([("до", old, 101), ("после", new, 0)] * 2, 1):
            rc = run(tree)
            ok = rc == want_rc
            print(f"e011: шаг {i} ({name}) rc={rc} ожидалось {want_rc}: {'ok' if ok else 'НЕТ'}", flush=True)
            if not ok:
                bad.append(i)
    finally:
        for tree in (old, new):
            subprocess.run(["git", "-C", REPO, "worktree", "remove", "--force", tree], capture_output=True)
        subprocess.run(["git", "-C", REPO, "worktree", "prune"], capture_output=True)
        shutil.rmtree(base, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
