#!/usr/bin/env python3
"""TK-065: посуточный скрипт сетки R2 (клетки, которые уже кодируются) по строке 3 базового jall-скрипта суток.
tk065-r2-gen.py <jall-скрипт> <сутки> <каталог-выхода>  -> пишет <кат>/r2-<сутки>.sh и r2-<сутки>.cells"""
import os, shlex, sys

jall, day, out = sys.argv[1:4]
DROP = {"--entry-form", "--stop-form", "--take-form", "--deadline-secs", "--exit-form", "--set", "--early-exit-secs",
        "--entry-ttl-secs", "--cells", "--out-dir", "--extra-runs"}
line = open(jall, encoding="utf-8").read().split("\n")[2]
tok = shlex.split(line.split(" > ")[0])
base, i = [], 0
while i < len(tok):
    if tok[i] in DROP:
        i += 2
        continue
    base.append(tok[i])
    i += 1

BASE_ENTRY = "ladder3x0..0.0409sw2"
SET_B1 = "t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55"
SET_G87 = "g87-fresh:age=2700,agemax=3600,side=bid,btc4h_max=-44.55"
exits = []
exits += [f"pyre{n}u{u}" for n in (3, 4, 5) for u in (1, 2, 3)]  # g92
exits += [f"pynw{k}u{u}" for u in (1, 2, 3) for k in (1, 2, 3)]  # g93
exits += [f"pyeat{n}" for n in (2, 3, 4, 5)]  # g94
exits += [f"conv{t}a20" for t in (0, 1, 2)]  # g119 (A = D20)
exits += ["halfstop", "halflevel"]  # g114, f = 1/2
tsl = [f"tsl{g}t{t}" for g in (5, 10, 20) for t in (1, 2, 4)]  # g117: база B3 (тейк 1to1, трейл выкл.) — отдельный скрипт r2t
fresh = [f"pyfresh{n}" for n in (2, 3, 4, 5)]  # g87
tail = "pct2-tr1x1-14400-ttl1800"
cells = [(f"{BASE_ENTRY}-{tail}", "t-bid-btc4h-q1"), (f"{BASE_ENTRY}-{tail}", "g87-fresh")]
cells += [(f"{BASE_ENTRY}-{tail}-{e}", "t-bid-btc4h-q1") for e in exits]
cells += [(f"{BASE_ENTRY}-{tail}-{e}", "g87-fresh") for e in fresh]
cells += [(f"ladder3x0..0.0409sw3-{tail}", "t-bid-btc4h-q1")]  # g95
os.makedirs(out, exist_ok=True)
cf = f"{out}/r2-{day}.cells"
open(cf, "w", newline="\n").write("".join(f"{c} {s}\n" for c, s in cells))
args = base + ["--entry-ttl-secs", "1800", "--entry-form", BASE_ENTRY, "--entry-form", "ladder3x0..0.0409sw3",
               "--stop-form", "pct2", "--take-form", "tr1x1", "--deadline-secs", "14400", "--exit-form", "none"]
for e in exits + fresh:
    args += ["--exit-form", e]
args += ["--set", SET_B1, "--set", SET_G87, "--cells", cf, "--out-dir", f"b5/.cellstmp-{day}"]
sh = ["set -e", f"rm -rf b5/.cellstmp-{day}",
      " ".join(shlex.quote(a) for a in args) + f" > b5/.cellstmp-{day}.log 2>&1",
      f"cp b5/.cellstmp-{day}.log {out}/r2-{day}.grid.log", ":",
      f"mkdir -p b5/r2/{day}", f"cp -r b5/.cellstmp-{day}/. b5/r2/{day}/"]
open(f"{out}/r2-{day}.sh", "w", newline="\n").write("\n".join(sh) + "\n")
print(len(cells), "клеток", cf)

# Г-117: сползающий тейк работает только при фиксированном тейке (trail_bps = 0) — база B3 = pct2-1to1, отдельный проход
tail3 = "pct2-1to1-14400-ttl1800"
cells3 = [(f"{BASE_ENTRY}-{tail3}", "t-bid-btc4h-q1")] + [(f"{BASE_ENTRY}-{tail3}-{e}", "t-bid-btc4h-q1") for e in tsl]
cf3 = f"{out}/r2t-{day}.cells"
open(cf3, "w", newline="\n").write("".join(f"{c} {s}\n" for c, s in cells3))
a3 = base + ["--entry-ttl-secs", "1800", "--entry-form", BASE_ENTRY, "--stop-form", "pct2", "--take-form", "1to1",
             "--deadline-secs", "14400", "--exit-form", "none"]
for e in tsl:
    a3 += ["--exit-form", e]
a3 += ["--set", SET_B1, "--cells", cf3, "--out-dir", f"b5/.cellstmp3-{day}"]
sh3 = ["set -e", f"rm -rf b5/.cellstmp3-{day}",
       " ".join(shlex.quote(a) for a in a3) + f" > b5/.cellstmp3-{day}.log 2>&1",
       f"cp b5/.cellstmp3-{day}.log {out}/r2t-{day}.grid.log", ":",
       f"mkdir -p b5/r2t/{day}", f"cp -r b5/.cellstmp3-{day}/. b5/r2t/{day}/"]
open(f"{out}/r2t-{day}.sh", "w", newline="\n").write("\n".join(sh3) + "\n")
print(len(cells3), "клеток Г-117 (B3)", cf3)
