#!/usr/bin/env python3
"""TK-115: посуточный скрипт «досчёта П-12» (e114 f=1/4,3/4; e106 nostop; g95-w3; e65) по строке 3 базового jall-скрипта суток.
tk115-gen.py <jall-скрипт> <сутки> <каталог-выхода> [--tape-log W] [--only a|b]  -> <кат>/r3-<сутки>.sh и .cells
a — клетки без журналов (e114 f, g95, e106); b — e65 (нужен walls-<SYM>.csv суток от `lob touches --wall-log`).
e112/e116 (пороги Исследователя) и e133 (W из прохода --tape-log) — отдельным заходом после прохода-журнала."""
import os, shlex, sys

jall, day, out = sys.argv[1:4]
opt = sys.argv[4:]
tape = opt[opt.index("--tape-log") + 1] if "--tape-log" in opt else None
only = opt[opt.index("--only") + 1] if "--only" in opt else "a"
DROP = {"--entry-form", "--stop-form", "--take-form", "--deadline-secs", "--exit-form", "--set", "--early-exit-secs",
        "--entry-ttl-secs", "--cells", "--out-dir", "--extra-runs", "--tape-log"}
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
# Г-106: популяция — стена переставляемая (born_shift_cbps определена; Q-7), граница ниже любого значения
SET_E106 = SET_B1.replace("t-bid-btc4h-q1", "e106-pop") + ",r1_born_shift_cbps_min=-9000000000000000000"
tail = "pct2-tr1x1-14400-ttl1800"
cells, entries, exits, sets = [], [BASE_ENTRY], [], [SET_B1]
if only == "a":
    exits += [f"{h}{f}" for h in ("halfstop", "halflevel") for f in ("f1", "f3")]  # e114, f = 1/4, 3/4
    exits += [f"nostop{x}" for x in (1, 2, 4)]  # e106-act0,5 / 1 / 2
    entries.append("ladder3x0..0.0409sw3")  # g95-w3
    cells += [(f"{BASE_ENTRY}-{tail}-{e}", "t-bid-btc4h-q1") for e in exits if not e.startswith("nostop")]
    cells += [(f"{BASE_ENTRY}-{tail}", "e106-pop")]
    cells += [(f"{BASE_ENTRY}-{tail}-{e}", "e106-pop") for e in exits if e.startswith("nostop")]
    cells += [(f"ladder3x0..0.0409sw3-{tail}", "t-bid-btc4h-q1")]
    sets.append(SET_E106)
else:
    exits += ["wall2", "wall2x"]  # e65-t2 (−1 тик), e65-t2x (+1 тик)
    cells += [(f"{BASE_ENTRY}-{tail}-{e}", "t-bid-btc4h-q1") for e in exits]
os.makedirs(out, exist_ok=True)
cf = f"{out}/r3{only}-{day}.cells"
open(cf, "w", newline="\n").write("".join(f"{c} {s}\n" for c, s in cells))
args = base + ["--entry-ttl-secs", "1800"]
for e in entries:
    args += ["--entry-form", e]
args += ["--stop-form", "pct2", "--take-form", "tr1x1", "--deadline-secs", "14400", "--exit-form", "none"]
for e in exits:
    args += ["--exit-form", e]
for s in sets:
    args += ["--set", s]
if tape:
    args += ["--tape-log", tape]
args += ["--cells", cf, "--out-dir", f"b5/.cellstmp-r3{only}-{day}"]
t = f"b5/.cellstmp-r3{only}-{day}"
sh = ["set -e", f"rm -rf {t}",
      " ".join(shlex.quote(a) for a in args) + f" > {t}.log 2>&1",
      f"cp {t}.log {out}/r3{only}-{day}.grid.log", ":",
      f"mkdir -p b5/r3{only}/{day}", f"cp -r {t}/. b5/r3{only}/{day}/"]
open(f"{out}/r3{only}-{day}.sh", "w", newline="\n").write("\n".join(sh) + "\n")
print(len(cells), "клеток", cf)
