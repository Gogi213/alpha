#!/usr/bin/env python3
"""TK-367: инвентарь данных Bybit янв–июн 2026 на calc. Только чтение.

Совпадение — по имени: дата 2026-01..06 (в т.ч. суточные файлы) или токен месяца jan..jun
(`e-jan`, `jan`, `jan.gen.log`). Совпавший каталог не раскрывается; совпавшие файлы агрегируются
по (родитель, месяц). Исключения — реестр, local-backup, SETTLED и т.п.
Выход: <out>/candidates.tsv (путь, тип, байты, файлов), <out>/files.lst (совпавшие файлы поштучно),
<out>/skipped.tsv (исключённые корни).
"""
import os
import re
import sys

ROOTS = sys.argv[1].split(":")
OUT = sys.argv[2]
MAXDEPTH = int(sys.argv[3]) if len(sys.argv) > 3 else 5

EXCLUDE = {
    "/data/registry", "/data/launch/registry", "/data/local-backup", "/data/lost+found",
    "/data/sched", "/data/board", "/data/progress",
}
DATE = re.compile(r"2026-0([1-6])(?!\d)")
TOKEN = re.compile(r"(?<![a-z])(jan|feb|mar|apr|may|jun)(?![a-z])")
MON = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06"}


def month_of(name):
    m = DATE.search(name)
    if m:
        return "0" + m.group(1)
    m = TOKEN.search(name.lower())
    if m:
        return MON[m.group(1)]
    return None


def du(path):
    total = files = 0
    for dp, dn, fn in os.walk(path, followlinks=False):
        for f in fn:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
                files += 1
            except OSError:
                pass
    return total, files


cand = []
agg = {}
flist = open(os.path.join(OUT, "files.lst"), "w", buffering=1)
cfile = open(os.path.join(OUT, "candidates.tsv"), "w", buffering=1)
cfile.write("path\tkind\tbytes\tfiles\tmonth\n")
skipped = []


def add(c):
    cand.append(c)
    cfile.write("%s\t%s\t%d\t%d\t%s\n" % c)


def walk(path, depth):
    try:
        it = list(os.scandir(path))
    except OSError:
        return
    for e in it:
        p = e.path
        if p in EXCLUDE or "/registry" in p and p.startswith("/data/launch"):
            skipped.append(p)
            continue
        mo = month_of(e.name)
        if e.is_symlink():
            if mo:
                add((p, "link", 0, 1, mo))
            continue
        if e.is_dir(follow_symlinks=False):
            if mo:
                b, n = du(p)
                add((p, "dir", b, n, mo))
            elif depth < MAXDEPTH:
                walk(p, depth + 1)
        elif mo:
            try:
                sz = e.stat(follow_symlinks=False).st_size
            except OSError:
                sz = 0
            k = (path, mo)
            a = agg.setdefault(k, [0, 0])
            a[0] += sz
            a[1] += 1
            flist.write(f"{p}\t{sz}\n")


for r in ROOTS:
    walk(r, 1)
flist.close()
with open(os.path.join(OUT, "candidates.tsv"), "w") as f:
    f.write("path\tkind\tbytes\tfiles\tmonth\n")
    for p, k, b, n, mo in sorted(cand):
        f.write(f"{p}\t{k}\t{b}\t{n}\t{mo}\n")
    for (par, mo), (b, n) in sorted(agg.items()):
        f.write(f"{par}/*[{mo}]\tfiles\t{b}\t{n}\t{mo}\n")
with open(os.path.join(OUT, "skipped.tsv"), "w") as f:
    f.write("\n".join(skipped) + "\n")
print("done", len(cand), len(agg))
