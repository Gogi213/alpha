#!/usr/bin/env python3
"""КТ-1 (TK-145): защита от россыпи. Новый `tools/compute/(tk|p)<цифра>*-*` — предупреждение (rc 0);
число отслеживаемых файлов в корне репо (`root_files=N`) или в корне tools/compute (`compute_root_files=N`) выше базы `tools/sprawl-base.txt` (N — число после КТ-9,
не придуманное) — отказ (rc 1). Нет базы — корень не проверяется. Новые скрипты — `git diff --diff-filter=A <base>...HEAD`."""
import os, re, subprocess, sys

PAT = re.compile(r"^tools/compute/(tk|p)[0-9][^/]*-[^/]*$")


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8").stdout


def check(repo, base_ref="master"):
    warns, fails = [], []
    for ln in git("diff", "--name-status", "--diff-filter=A", f"{base_ref}...HEAD", cwd=repo).splitlines():
        f = ln.split("\t")[-1]
        if PAT.match(f):
            warns.append(f"новый скрипт-россыпь: {f} (используй warm-run/cold/analyze, не tk*-скрипт)")
    bp = os.path.join(repo, "tools", "sprawl-base.txt")
    if os.path.exists(bp):
        base = int(re.search(r"(?<![\w])root_files=(\d+)", open(bp, encoding="utf-8").read()).group(1))
        n = sum("/" not in f for f in git("ls-files", cwd=repo).splitlines())
        if n > base:
            fails.append(f"файлов в корне {n} > базы {base}")
        cb = re.search(r"compute_root_files=(\d+)", open(bp, encoding="utf-8").read())
        if cb:
            nc = sum(f.count("/") == 2 for f in git("ls-files", "tools/compute", cwd=repo).splitlines())
            if nc > int(cb.group(1)):
                fails.append(f"файлов в tools/compute (корень) {nc} > базы {cb.group(1)}")
    return warns, fails


if __name__ == "__main__":
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    w, f = check(repo, sys.argv[2] if len(sys.argv) > 2 else "master")
    for x in w:
        print("предупреждение:", x)
    for x in f:
        print("ОТКАЗ:", x)
    sys.exit(1 if f else 0)
