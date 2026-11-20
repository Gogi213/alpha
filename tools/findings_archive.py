#!/usr/bin/env python3
"""Archive stale docs/findings entries and keep links valid (TK-640).

  python tools/findings_archive.py plan  [--since YYYY-MM-DD]   # live / stale table -> stdout
  python tools/findings_archive.py apply [--since YYYY-MM-DD]   # git mv stale -> docs/archive/findings, rewrite links
  python tools/findings_archive.py check                        # broken repo-path links to docs/findings & docs/archive/findings

Unit = top-level entry of docs/findings (file or directory). An entry is live if referenced from
LIVE_SOURCES, open/running kb cards, src/ (code comments are not rewritten), tools/ (a tool may read it),
dated >= --since (name date, else first-add commit date), or KEEP globs; plus files referenced by live .md.
Stdlib only.
"""
import argparse
import fnmatch
import os
import re
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIND = "docs/findings"
ARCH = "docs/archive/findings"
LIVE_SOURCES = [
    "AGENTS.md", "docs/COMMANDS.md", "docs/ARCHITECTURE.md",
    "docs/research/P-42-level-breakout.md", "docs/research/freeze/*",
    "docs/plan/coord-handoff.md", "docs/plan/G-022-handoff-2026-11-13.md",
    ".claude/preferences.md", ".claude/skills/*",
]
CODE_SOURCES = ["src/*", "Cargo.toml", "tools/*"]  # tools/analyze, tools/*archive: one-off, paths rewritten
KEEP = ["README.md", "p42-tradecount-size-2026-11-20*"]  # TK-639 in progress on another branch
TEXT_EXT = {".md", ".py", ".sh", ".rs", ".toml", ".json", ".jsonl", ".csv", ".txt", ".js", ".html",
            ".yml", ".yaml", ".service", ".timer", ".conf", ".tsv", ".ipynb", ".sql", ".mjs", ".css"}
# repo-root path; the terminator set excludes chars that never appear in our file names
REF = re.compile(r"docs/(?:archive/)?findings/([^\s)\]>\"'`,;|*{}<\\]+)")
MDLINK = re.compile(r"(\]\(|src=\"|href=\")([^)\"#\s]+)")
DATE = re.compile(r"(20\d\d)-(\d\d)-(\d\d)")


def git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout


def tracked():
    return [p for p in git("ls-files", "-z").split("\0") if p]


def is_text(p):
    return Path(p).suffix.lower() in TEXT_EXT or Path(p).name in ("AGENTS.md", "CLAUDE.md")


def read(p):
    try:
        return (ROOT / p).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


def entries():
    return sorted(os.listdir(ROOT / FIND))


def top(rel):
    """'a/b.png' -> 'a'; strips trailing punctuation that prose glues to a path."""
    return rel.split("/")[0].rstrip(".:")


def refs_in(text, path, names):
    """Top-level findings entries referenced by text (repo-root paths + relative md links)."""
    out = set()
    for m in REF.finditer(text):
        t = top(m.group(1))
        if t in names:
            out.add(t)
    base = (ROOT / path).parent
    for m in MDLINK.finditer(text):
        tgt = m.group(2)
        if "://" in tgt or tgt.startswith(("mailto:", "/")):
            continue
        try:
            rel = (base / tgt).resolve().relative_to(ROOT / FIND)
        except (ValueError, OSError):
            continue
        t = rel.parts[0] if rel.parts else ""
        if t in names:
            out.add(t)
    return out


def match_any(p, globs):
    return any(fnmatch.fnmatch(p, g) for g in globs)


def kb_live_cards(files):
    live = []
    for p in files:
        if p.startswith("kb/") and p.endswith(".md") and "/_templates/" not in p:
            t = read(p) or ""
            m = re.search(r"^status:\s*(\w+)", t, re.M)
            if m and m.group(1) in ("open", "running"):
                live.append(p)
    return live


def added_dates():
    """first-add commit date per top-level entry (git log, oldest wins)."""
    out = {}
    cur = None
    for line in git("log", "--diff-filter=A", "--name-only", "--format=@%as", "--", FIND).splitlines():
        if line.startswith("@"):
            cur = line[1:]
        elif line.startswith(FIND + "/"):
            out[top(line[len(FIND) + 1:])] = cur  # log is newest-first: last write = oldest
    return out


def classify(since):
    files = tracked()
    names = set(entries())
    reason = {}

    def mark(t, why):
        reason.setdefault(t, why)

    for g in KEEP:
        for n in names:
            if fnmatch.fnmatch(n, g):
                mark(n, "keep-list")
    srcs = [(p, "live-doc") for p in files if match_any(p, LIVE_SOURCES)]
    srcs += [(p, "kb-open") for p in kb_live_cards(files)]
    srcs += [(p, "code") for p in files if match_any(p, CODE_SOURCES) and is_text(p)
             and not p.startswith(("tools/compute/archive/", "tools/archive/", "tools/analyze/"))]
    for p, why in srcs:
        t = read(p)
        if t:
            for n in refs_in(t, p, names):
                mark(n, f"{why}:{p}")
    ad = added_dates()
    for n in names:
        m = DATE.search(n)
        d = m.group(0) if m else ad.get(n)
        if d and d >= since:
            mark(n, f"recent:{d}")
    # closure: whatever a live .md (or .md inside a live dir) points to stays next to it
    todo = list(reason)
    while todo:
        n = todo.pop()
        p = ROOT / FIND / n
        mds = [p] if p.suffix == ".md" else (list(p.rglob("*.md")) if p.is_dir() else [])
        for md in mds:
            t = md.read_text(encoding="utf-8", errors="replace")
            for r in refs_in(t, md.relative_to(ROOT).as_posix(), names):
                if r not in reason:
                    reason[r] = f"linked-from:{n}"
                    todo.append(r)
    return names, reason


def size(p):
    p = ROOT / p
    return p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def cmd_plan(a):
    names, reason = classify(a.since)
    keep = sorted(reason)
    stale = sorted(names - set(reason))
    mb = lambda xs: sum(size(f"{FIND}/{x}") for x in xs) / 1e6
    for n in keep:
        print(f"KEEP\t{n}\t{reason[n]}")
    for n in stale:
        print(f"MOVE\t{n}")
    print(f"# keep {len(keep)} ({mb(keep):.1f} MB) · move {len(stale)} ({mb(stale):.1f} MB)", file=sys.stderr)


def rewrite_text(text, path_old, path_new, moved):
    """repo-root refs to moved entries -> archive; relative md links re-based for both ends moving."""
    def root_sub(m):
        whole, rel = m.group(0), m.group(1)
        if whole.startswith("docs/archive/"):
            return whole
        return f"{ARCH}/{rel}" if top(rel) in moved else whole

    text = REF.sub(root_sub, text)
    if not path_old.endswith((".md", ".html")):
        return text
    base_old, base_new = (ROOT / path_old).parent, (ROOT / path_new).parent

    def link_sub(m):
        pre, tgt = m.group(1), m.group(2)
        if "://" in tgt or tgt.startswith(("mailto:", "/")) or "findings/" in tgt and tgt.startswith("docs/"):
            return m.group(0)
        try:
            ab = (base_old / tgt).resolve()
            rel = ab.relative_to(ROOT).as_posix()
        except (ValueError, OSError):
            return m.group(0)
        if rel.startswith(FIND + "/") and top(rel[len(FIND) + 1:]) in moved:
            rel = ARCH + rel[len(FIND):]
        new = os.path.relpath(ROOT / rel, base_new).replace("\\", "/")
        if base_old == base_new and rel == ab.relative_to(ROOT).as_posix():
            return m.group(0)
        return pre + new

    return MDLINK.sub(link_sub, text)


def cmd_apply(a):
    names, reason = classify(a.since)
    moved = names - set(reason)
    (ROOT / ARCH).mkdir(parents=True, exist_ok=True)
    tracked_set = set(tracked())
    old_new = {}
    for p in tracked_set:
        if p.startswith(FIND + "/") and top(p[len(FIND) + 1:]) in moved:
            old_new[p] = ARCH + p[len(FIND):]
    # rewrite first (paths still at old place), then move
    changed = 0
    for p in sorted(tracked_set):
        if not is_text(p) or p.startswith(("src/", "Cargo.toml")):
            continue
        t = read(p)
        if t is None:
            continue
        n = rewrite_text(t, p, old_new.get(p, p), moved)
        if n != t:
            with open(ROOT / p, "w", encoding="utf-8", newline="") as f:
                f.write(n)
            changed += 1
    for n in sorted(moved):
        src = f"{FIND}/{n}"
        if any(k == src or k.startswith(src + "/") for k in old_new):
            git("mv", "-k", src, f"{ARCH}/{n}")
        # untracked leftovers (if any) stay; reported by plan
    print(f"moved {len(moved)} entries ({len(old_new)} files), rewrote {changed} files")


def cmd_check(a):
    bad = 0
    files = set(tracked())
    exists = lambda r: (ROOT / r).exists()
    for p in sorted(files):
        if not is_text(p):
            continue
        t = read(p)
        if not t:
            continue
        for m in REF.finditer(t):
            r = m.group(0).rstrip(".:")
            if "<" in r or "YYYY" in r or "NNN" in r:
                continue
            if not exists(r) and not exists(r.split("#")[0]):
                print(f"{p}: {r}")
                bad += 1
        if p.startswith((FIND + "/", ARCH + "/")) and p.endswith(".md"):
            for m in MDLINK.finditer(t):
                tgt = m.group(2)
                if "://" in tgt or tgt.startswith(("mailto:", "/")) or tgt.startswith("docs/"):
                    continue
                if not (ROOT / p).parent.joinpath(tgt).exists():
                    print(f"{p}: ({tgt})")
                    bad += 1
    print(f"broken: {bad}", file=sys.stderr)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "apply", "check"])
    ap.add_argument("--since", default=(date.today() - timedelta(days=2)).isoformat())
    a = ap.parse_args()
    return {"plan": cmd_plan, "apply": cmd_apply, "check": cmd_check}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
