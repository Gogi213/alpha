#!/usr/bin/env python3
"""TK-479: проверки приёмки ядра statarb, исполнимые с любой машины (Git Bash берётся полным путём, не WSL-`bash`).
  accept.py sprawl          корень tools/compute: файлов в ветке не выше, чем в master (rc 1 при росте)
  accept.py fmt             cargo fmt --check
  accept.py test            vps-check.sh . test statarb (calc), rc = код проверки
  accept.py clippy          vps-check.sh . clippy (calc), rc = код проверки
  accept.py log <id>        готовый лог заявки calc /data/sched/logs/<id>.log: rc 0 и строка `bench kind=` (по ssh)
  accept.py lint            sprawl + fmt (быстрое, без calc)"""
import os
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
GIT_BASH = os.environ.get("GIT_BASH", "C:/Program Files/Git/bin/bash.exe")
SSH = ["ssh", "-i", "C:/Users/Георгий/.ssh/id_rsa", "-o", "UserKnownHostsFile=C:/Users/Георгий/.ssh/known_hosts",
       "-o", "ConnectTimeout=15", "root@89.163.242.211"]


def git(*a):
    return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, encoding="utf-8").stdout


def root_count(ref):
    """Файлов прямо в tools/compute (глубина 2 в пути) — как считает tools/check-sprawl.py."""
    files = git("ls-tree", "-r", "--name-only", ref, "tools/compute").splitlines()
    return sum(f.count("/") == 2 for f in files)


def sprawl():
    b, m = root_count("HEAD"), root_count("master")
    print(f"tools/compute (корень): ветка {b}, master {m}")
    if b > m:
        print("ОТКАЗ: ветка выше master")
        return 1
    return 0


def run(cmd):
    return subprocess.run(cmd, cwd=REPO).returncode


def vps(what, *extra):
    return run([GIT_BASH, "tools/vps-check.sh", ".", what, *extra])


def log(job):
    out = subprocess.run(SSH + [f"cat /data/sched/rc/{job}; grep -c 'bench kind=' /data/sched/logs/{job}.log"],
                         capture_output=True, text=True, encoding="utf-8")
    parts = out.stdout.split()
    ok = out.returncode == 0 and len(parts) >= 2 and parts[0] == "0" and int(parts[1]) > 0
    print(f"заявка {job}: rc={parts[0] if parts else '?'}, строк bench={parts[1] if len(parts) > 1 else '?'}",
          "— да" if ok else "— нет")
    return 0 if ok else 1


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "sprawl":
        return sprawl()
    if cmd == "fmt":
        return run(["cargo", "fmt", "--check"])
    if cmd == "lint":
        return sprawl() or run(["cargo", "fmt", "--check"])
    if cmd == "test":
        return vps("test", "statarb")
    if cmd == "clippy":
        return vps("clippy")
    if cmd == "log" and len(argv) > 2 and re.fullmatch(r"\d+", argv[2]):
        return log(argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
