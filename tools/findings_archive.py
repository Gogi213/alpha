#!/usr/bin/env python3
"""Archive stale docs/findings entries and keep links valid (TK-640).

  python tools/findings_archive.py plan  [--since YYYY-MM-DD]   # live / stale table -> stdout
  python tools/findings_archive.py apply [--since YYYY-MM-DD]   # git mv stale -> archive, live-again back; rewrite links
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


def entries(d=FIND):
    return sorted(os.listdir(ROOT / d)) if (ROOT / d).is_dir() else []


def top(rel):
    """'a/b.png' -> 'a'; drops ':68,451' / '#L5' suffixes and trailing punctuation prose glues to a path."""
    return re.sub(r"[:#].*$", "", rel.split("/")[0]).rstrip(".:")


def refs_in(text, path, names):
    """(entry, form) referenced by text; form 'f' = docs/findings/..., 'a' = docs/archive/findings/...
    (repo-root paths + relative md links)."""
    out = set()
    for m in REF.finditer(text):
        t = top(m.group(1))
        form = "a" if m.group(0).startswith("docs/archive/") else "f"
        if t in names:
            out.add((t, form))
        elif text[m.end():m.end() + 1] == "*" and "/" not in m.group(1) and len(t) >= 8:
            out.update((n, form) for n in names if n.startswith(t))  # `docs/findings/verify-gate-v177-*.csv`
    base = (ROOT / path).parent
    for m in MDLINK.finditer(text):
        tgt = m.group(2)
        if "://" in tgt or tgt.startswith(("mailto:", "/")):
            continue
        for d, form in ((FIND, "f"), (ARCH, "a")):
            try:
                rel = (base / tgt).resolve().relative_to(ROOT / d)
            except (ValueError, OSError):
                continue
            t = rel.parts[0] if rel.parts else ""
            if t in names:
                out.add((t, form))
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
    """-> (names in docs/findings, names in archive, {live entry: reason}).
    An archived entry is live again only via a docs/findings/... reference (one that expects it back)."""
    files = tracked()
    names_f, names_a = set(entries(FIND)), set(entries(ARCH))
    names = names_f | names_a
    reason = {}

    def mark(t, why):
        reason.setdefault(t, why)

    def hits(text, path):
        return {t for t, form in refs_in(text, path, names) if t in names_f or form == "f"}

    for g in KEEP:
        for n in names_f:
            if fnmatch.fnmatch(n, g):
                mark(n, "keep-list")
    srcs = [(p, "live-doc") for p in files if match_any(p, LIVE_SOURCES)]
    srcs += [(p, "kb-open") for p in kb_live_cards(files)]
    srcs += [(p, "code") for p in files if match_any(p, CODE_SOURCES) and is_text(p)
             and not p.startswith(("tools/compute/archive/", "tools/archive/", "tools/analyze/"))]
    for p, why in srcs:
        t = read(p)
        if t:
            for n in hits(t, p):
                mark(n, f"{why}:{p}")
    ad = added_dates()
    for n in names_f:
        m = DATE.search(n)
        d = m.group(0) if m else ad.get(n)
        if d and d >= since:
            mark(n, f"recent:{d}")
    # closure: whatever a live .md (or .md inside a live dir) points to stays next to it
    todo = list(reason)
    while todo:
        n = todo.pop()
        p = ROOT / (FIND if n in names_f else ARCH) / n
        mds = [p] if p.suffix == ".md" else (list(p.rglob("*.md")) if p.is_dir() else [])
        for md in mds:
            t = md.read_text(encoding="utf-8", errors="replace")
            for r in hits(t, md.relative_to(ROOT).as_posix()):
                if r not in reason:
                    reason[r] = f"linked-from:{n}"
                    todo.append(r)
    return names_f, names_a, reason


def size(p):
    p = ROOT / p
    return p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def cmd_plan(a):
    names_f, names_a, reason = classify(a.since)
    keep = sorted(n for n in reason if n in names_f)
    back = sorted(n for n in reason if n in names_a)
    stale = sorted(names_f - set(reason))
    mb = lambda xs: sum(size(f"{FIND}/{x}") for x in xs) / 1e6
    for n in keep:
        print(f"KEEP\t{n}\t{reason[n]}")
    for n in back:
        print(f"BACK\t{n}\t{reason[n]}")
    for n in stale:
        print(f"MOVE\t{n}")
    print(f"# keep {len(keep)} ({mb(keep):.1f} MB) · back {len(back)} · move {len(stale)} ({mb(stale):.1f} MB)",
          file=sys.stderr)


def rewrite_text(text, path_old, path_new, to_arch, to_find):
    """repo-root refs follow their entry (findings <-> archive); relative md links re-based."""
    def root_sub(m):
        whole, rel = m.group(0), m.group(1)
        if whole.startswith("docs/archive/"):
            return f"{FIND}/{rel}" if top(rel) in to_find else whole
        return f"{ARCH}/{rel}" if top(rel) in to_arch else whole

    text = REF.sub(root_sub, text)
    if not path_old.endswith((".md", ".html")):
        return text
    base_old, base_new = (ROOT / path_old).parent, (ROOT / path_new).parent

    def link_sub(m):
        pre, tgt = m.group(1), m.group(2)
        if "://" in tgt or tgt.startswith(("mailto:", "/")) or "findings/" in tgt and tgt.startswith("docs/"):
            return m.group(0)
        try:
            orig = (base_old / tgt).resolve().relative_to(ROOT).as_posix()
        except (ValueError, OSError):
            return m.group(0)
        rel = orig
        if rel.startswith(FIND + "/") and top(rel[len(FIND) + 1:]) in to_arch:
            rel = ARCH + rel[len(FIND):]
        elif rel.startswith(ARCH + "/") and top(rel[len(ARCH) + 1:]) in to_find:
            rel = FIND + rel[len(ARCH):]
        if base_old == base_new and rel == orig:
            return m.group(0)
        return pre + os.path.relpath(ROOT / rel, base_new).replace("\\", "/")

    return MDLINK.sub(link_sub, text)


def cmd_apply(a):
    names_f, names_a, reason = classify(a.since)
    to_arch = names_f - set(reason)
    to_find = names_a & set(reason)
    # leftover docs/findings/... refs to entries already archived are redirected too
    arch_final = (names_a - to_find) | to_arch
    (ROOT / ARCH).mkdir(parents=True, exist_ok=True)
    tracked_set = set(tracked())
    old_new = {}
    for p in tracked_set:
        if p.startswith(FIND + "/") and top(p[len(FIND) + 1:]) in to_arch:
            old_new[p] = ARCH + p[len(FIND):]
        elif p.startswith(ARCH + "/") and top(p[len(ARCH) + 1:]) in to_find:
            old_new[p] = FIND + p[len(ARCH):]
    # rewrite first (paths still at old place), then move
    changed = 0
    for p in sorted(tracked_set):
        if not is_text(p) or p.startswith(("src/", "Cargo.toml")):
            continue
        t = read(p)
        if t is None:
            continue
        n = rewrite_text(t, p, old_new.get(p, p), arch_final, to_find)
        if n != t:
            with open(ROOT / p, "w", encoding="utf-8", newline="") as f:
                f.write(n)
            changed += 1
    for names, src_d, dst_d in ((to_arch, FIND, ARCH), (to_find, ARCH, FIND)):
        for n in sorted(names):
            src = f"{src_d}/{n}"
            if any(k == src or k.startswith(src + "/") for k in old_new):
                git("mv", "-k", src, f"{dst_d}/{n}")
    print(f"archived {len(to_arch)}, restored {len(to_find)} entries ({len(old_new)} files), rewrote {changed} files")


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
            r = re.sub(r"[:#]\d.*$", "", m.group(0)).rstrip(".:")
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
