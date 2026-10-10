#!/usr/bin/env python3
"""TK-046: сверка rounds.csv суток новым домом с декой — по общим монетам, строки побайтно. [MON=aug|jan|feb] python3 tk046-cmp.py <сутки>"""
import csv, glob, os, sys, collections
day = sys.argv[1]; MON = os.environ.get("MON", "aug")
M = f"/data/tk046/{MON}/home/alpha/epochs/e-{MON}/b5"; D = f"/home/deck/alpha/epochs/e-{MON}/b5"
def load(p):
    with open(p, newline="") as f:
        lines = [l for l in f if not l.startswith("#")]
    hdr = lines[0].rstrip("\n").split(",")
    i = hdr.index("symbol") if "symbol" in hdr else 0
    g = collections.defaultdict(list)
    for l in lines[1:]:
        g[l.split(",")[i]].append(l)
    return g
tot = same = nofile = 0; bad = []; nsym = 0
for p in sorted(glob.glob(f"{M}/*/{day}/*/rounds.csv")):
    rel = os.path.relpath(p, M); q = f"{D}/{rel}"
    if not os.path.exists(q):
        nofile += 1; continue
    a, b = load(p), load(q); tot += 1
    com = set(a) & set(b); nsym += len(com)
    diff = [s for s in com if a[s] != b[s]]
    if diff: bad.append((rel, len(com), len(diff), diff[:3]))
    else: same += 1
print(f"{day}: файлов rounds.csv у обоих {tot}, совпали по общим монетам {same}, расходятся {len(bad)}, нет у деки {nofile}, пар файл-монета {nsym}")
for b in bad[:25]: print(*b)
