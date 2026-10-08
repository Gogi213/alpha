#!/usr/bin/env python3
"""TK-084 п.4: посуточный скрипт 9 клеток П-12 (x-stop, x-deadline, x-entry) по строке 3 базового jall-скрипта суток.
tk084-p12-gen.py <jall-скрипт> <сутки> <каталог-выхода>  -> <кат>/p12-<сутки>.sh и p12-<сутки>.cells"""
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

B1 = "ladder3x0..0.0409sw2"
SET_B1 = "t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55"
entries = ["ladder3x0.00409..0.02045sw2", "ladder3x0.00409..0.0818sw2", "ladder3x0.010225..0.02045sw2",
           "ladder3x0.010225..0.0818sw2"]
cells = [(f"{B1}-pct{s}-tr1x1-14400-ttl1800", "t-bid-btc4h-q1") for s in ("2.5", "3", "4")]
cells += [(f"{B1}-pct2-tr1x1-{d}-ttl1800", "t-bid-btc4h-q1") for d in (21600, 28800)]
cells += [(f"{e}-pct2-tr1x1-14400-ttl1800", "t-bid-btc4h-q1") for e in entries]
os.makedirs(out, exist_ok=True)
cf = f"{out}/p12-{day}.cells"
open(cf, "w", newline="\n").write("".join(f"{c} {s}\n" for c, s in cells))
args = base + ["--entry-ttl-secs", "1800"]
for e in [B1] + entries:
    args += ["--entry-form", e]
for s in ("pct2", "pct2.5", "pct3", "pct4"):
    args += ["--stop-form", s]
args += ["--take-form", "tr1x1"]
for d in (14400, 21600, 28800):
    args += ["--deadline-secs", str(d)]
args += ["--exit-form", "none", "--set", SET_B1, "--cells", cf, "--out-dir", f"b5/.cellstmp-p12-{day}"]
sh = ["set -e", f"rm -rf b5/.cellstmp-p12-{day}",
      " ".join(shlex.quote(a) for a in args) + f" > b5/.cellstmp-p12-{day}.log 2>&1",
      f"cp b5/.cellstmp-p12-{day}.log {out}/p12-{day}.grid.log", ":",
      f"mkdir -p b5/p12/{day}", f"cp -r b5/.cellstmp-p12-{day}/. b5/p12/{day}/"]
open(f"{out}/p12-{day}.sh", "w", newline="\n").write("\n".join(sh) + "\n")
print(len(cells), "клеток", cf)
