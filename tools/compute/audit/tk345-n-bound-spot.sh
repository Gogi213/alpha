#!/bin/bash
# TK-345 §10: n по кэшу подходов для спота (П-21) и спот→фьюч (П-22) с тиком/лотом из заголовка бинлога (HDR_ROOT).
cd /data/tk0345 || exit 1
md5sum n-bound2.py
J=/data/tk0319/jrn
nb() { # <сутки> <мес> <каталог выходов суток> <вывод>
  HDR_ROOT=/data/tk0319/e-$2/root python3 -I n-bound2.py $J/$1/wt/sub/D20/$1 /data/tk0319/e-$2/root/instruments.csv "$3" > "$4" 2>&1
}
nb 2026-02-10 feb /data/tk0313/out/run-feb-2026-02-10/out/2026-02-10 spot2-p21-feb.txt
nb 2026-04-01 apr /data/tk0313/out/run-apr-2026-04-01/out/2026-04-01 spot2-p21-apr.txt
for d in 2026-04-02 2026-04-08; do
  nb $d apr /data/tk0332-b/outB/run-B0-apr-$d/out/$d spot2-p22-$d.txt
done
tail -n 3 spot2-*.txt
