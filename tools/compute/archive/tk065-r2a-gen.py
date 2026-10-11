#!/usr/bin/env python3
"""TK-065: посуточные скрипты ТОЛЬКО семей A (pyre/pynw/pyeat/pyfresh) — пересчёт после правки сдвига стопа/тейка (Судья 08.10).
tk065-r2a-gen.py <каталог days> -> по каждым суткам из days.tsv читает r2-<сутки>.sh/.cells, пишет r2a-<сутки>.sh/.cells
(без базы, sw3, conv, half*; выход b5/r2a/<сутки>)."""
import shlex
import sys

out = sys.argv[1]
KEEP = ("-pyre", "-pynw", "-pyeat", "-pyfresh")
n = 0
for ln in open(f"{out}/days.tsv", encoding="utf-8"):
    day = ln.split()[0]
    cells = [c for c in open(f"{out}/r2-{day}.cells", encoding="utf-8") if any(k in c.split()[0] for k in KEEP)]
    cf = f"{out}/r2a-{day}.cells"
    open(cf, "w", newline="\n").write("".join(cells))
    keep_forms = {c.split()[0].rsplit("-", 1)[1] for c in cells}
    lines = open(f"{out}/r2-{day}.sh", encoding="utf-8").read().split("\n")
    tok = shlex.split(lines[2].split(" > ")[0])
    args, i = [], 0
    while i < len(tok):
        t = tok[i]
        if t == "--exit-form" and tok[i + 1] != "none" and tok[i + 1] not in keep_forms:
            i += 2
            continue
        if t == "--entry-form" and tok[i + 1].endswith("sw3"):
            i += 2
            continue
        if t == "--cells":
            args += [t, cf]
            i += 2
            continue
        if t == "--out-dir":
            args += [t, f"b5/.cellstmpa-{day}"]
            i += 2
            continue
        args.append(t)
        i += 1
    sh = ["set -e", f"rm -rf b5/.cellstmpa-{day}",
          " ".join(shlex.quote(a) for a in args) + f" > b5/.cellstmpa-{day}.log 2>&1",
          f"cp b5/.cellstmpa-{day}.log {out}/r2a-{day}.grid.log", ":",
          f"mkdir -p b5/r2a/{day}", f"cp -r b5/.cellstmpa-{day}/. b5/r2a/{day}/"]
    open(f"{out}/r2a-{day}.sh", "w", newline="\n").write("\n".join(sh) + "\n")
    n += 1
print(n, "суток;", len(cells), "клеток в последних")
