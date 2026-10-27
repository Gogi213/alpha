#!/usr/bin/env python3
"""TK-430: из вывода tk430-pick.py — выборка по обороту: артефакт (каждый N-й по quote_v) и топ без артефакта."""
import sys

rows = [l.rstrip("\n").split("\t") for l in open(sys.argv[1])]
step = int(sys.argv[2]) if len(sys.argv) > 2 else 25
bad = sorted([r for r in rows if r[1] == "1"], key=lambda r: float(r[3]))
ok = sorted([r for r in rows if r[1] == "0"], key=lambda r: -float(r[3]))
print(len(bad), len(ok))
for r in bad[::step]:
    print("BAD", *r)
for r in ok[:10]:
    print("OK", *r)
