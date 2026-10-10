#!/usr/bin/env python3
"""КТ-9 (TK-152): опись корня tools/compute — кто на какой скрипт ссылается.
Класс: ЯДРО (ссылка из src/, tools/ вне compute, COMMANDS/ARCHITECTURE, диспетчера, hooks, CLAUDE.md),
ЗАМЫКАНИЕ (ссылка из не-done тикета или из другого ЯДРА/ЗАМЫКАНИЯ корня), АРХИВ (иначе — кандидат на перенос).
Выход: TSV `файл<TAB>класс<TAB>кто ссылается (ЯДРО — вызывающий; ЗАМЫКАНИЕ — ссылающийся)` в stdout."""
import re, subprocess, sys, os

def git(*a):
    return subprocess.run(["git", *a], capture_output=True, text=True, encoding="utf-8").stdout

files = git("ls-files").splitlines()
root = [f for f in files if f.count("/") == 2 and f.startswith("tools/compute/")]
TEXT = (".sh", ".py", ".md", ".rs", ".toml", ".json", ".txt", ".yml", ".yaml", ".service", ".csv", ".ps1", ".info", ".html")
body = {}
for f in files:
    if f.startswith("tools/compute/archive/") or f.startswith("docs/archive/") or f.startswith(".claude/tickets/archive/"):
        continue
    if f.endswith(TEXT) or "." not in os.path.basename(f):
        try:
            body[f] = open(f, encoding="utf-8", errors="replace").read()
        except OSError:
            pass

def open_ticket(f):
    if not re.match(r"\.claude/tickets/TK-\d+\.md$", f):
        return False
    m = re.search(r"^status:\s*(\S+)", body[f], re.M)
    return not (m and m.group(1) == "done")

def kind(f):
    if f.startswith(("src/", "Cargo")) or (f.startswith("tools/") and not f.startswith("tools/compute/")):
        return "core"
    if f in ("docs/COMMANDS.md", "docs/ARCHITECTURE.md", "CLAUDE.md") or f.startswith((".claude/dispatcher/", ".claude/hooks/", ".claude/skills/")):
        return "core"
    if open_ticket(f):
        return "ticket"
    if f.startswith("tools/compute/"):
        return "script"
    return "other"

TOK = re.compile(r"[\w.-]+")
toks = {f: {x.rstrip(".-") for x in TOK.findall(t)} for f, t in body.items()}
rootset = set(root)
refs = {}
for r in root:
    name = os.path.basename(r)
    refs[r] = {f for f, t in toks.items() if f != r and name in t}
cls = {}
for r in root:
    ks = {kind(f) for f in refs[r]}
    cls[r] = "ЯДРО" if "core" in ks else "ЗАМЫКАНИЕ" if "ticket" in ks else None
for _ in range(5):  # транзитивно: ссылка из живого скрипта корня
    for r in root:
        if cls[r] is None and any(f in rootset and cls.get(f) in ("ЯДРО", "ЗАМЫКАНИЕ") for f in refs[r]):
            cls[r] = "ЗАМЫКАНИЕ"
def who_of(r):
    c = cls[r] or "АРХИВ"
    want = (lambda f: kind(f) == "core") if c == "ЯДРО" else (lambda f: kind(f) == "ticket" or (f in rootset and cls.get(f) in ("ЯДРО", "ЗАМЫКАНИЕ")))
    pick = sorted(f for f in refs[r] if want(f)) or sorted(refs[r])
    return ",".join(f.replace("tools/compute/", "") for f in pick[:4])

for r in sorted(root):
    print(f"{r}	{cls[r] or 'АРХИВ'}	{who_of(r)}")
