#!/bin/bash
# TK-048: perf-профиль декода суток (replay_compact_into_until, DIRECT_FEED=1) на пробе event-cache-probe; 1 поток, второй прогон тёплый.
BIN=${BIN:-/opt/alpha-compute/bin/alpha-b24probe}
O=/data/tk048/decprof; mkdir -p $O; rm -f $O/done
f=/data/tk046/jan/home/alpha/epochs/e-jan/study/root-2026-01-02/RIVERUSDT-2026-01-02.binlog
export ALPHA_DIRECT_FEED=1
$BIN lob event-cache-probe --binlog "$f" --out-json $O/warm.json > $O/warm.txt 2>&1
perf record -F 999 -g -o $O/perf.data -- $BIN lob event-cache-probe --binlog "$f" --out-json $O/run.json > $O/run.txt 2>&1
perf report -i $O/perf.data --no-children --sort symbol --stdio -g none 2>/dev/null | grep -v "^#" | grep -v "^$" | head -40 > $O/top.txt
touch $O/done
