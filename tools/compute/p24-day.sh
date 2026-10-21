#!/usr/bin/env bash
# TK-344 П-24 §2: одни сутки × 8 клеток (OFAT, один --cells), касания --signal touch из кэша study/approaches/D20, --busy-skip off (signals.csv
# для busy-replay с keep-q90/q95 шага 0). Набор all = side=bid (длинный; --side с --set запрещён). TRX исключён (В-105).
#   p24-day.sh <сутки>   env: BIN O GT V CELLS
set -uo pipefail
d=$1
BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk0289c-pgo}; O=${O:-/data/tk0344/p24/run}; GT=${GT:-1}; V=${V:-/data/tk044/final3/verdict.csv}; CELLS=${CELLS:-/data/tk0344/p24/cells.txt}
mon=$(case ${d:5:2} in 01) echo jan;; 02) echo feb;; 03) echo mar;; 04) echo apr;; 05) echo may;; 06) echo jun;; 07) echo jul;; 08) echo aug;; 09) echo sep;; esac)
H=/data/tk046/$mon/home; E=$H/alpha/epochs/e-$mon
[ -e "$O/$d/done" ] && exit 0
rm -rf "$O/$d"; mkdir -p "$O/$d"
awk -F, -v d="$d" 'NR==1||($2==d&&$1!="TRXUSDT")' "$V" > "$O/$d/verdict.csv"
export HOME=$H
t1=$(date +%s)
cd "$E" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$O/$d/verdict.csv" --root "study/root-$d" --touches-from study/approaches/D20 \
  --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
  --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide \
  --entry-form single@fr --stop-form before --stop-form pct0.1 --stop-form pct0.5 --take-form 1to1 --deadline-secs 1800 --deadline-secs 14400 \
  --exit-form eat50 --exit-form gone50 --exit-form none --early-exit-secs 2 --early-exit-secs 1 --early-exit-secs off \
  --set all:side=bid --cells "$CELLS" --out-dir "$O/$d/out" > "$O/$d/grid.log" 2>&1
rc=$?
echo "$d rc=$rc s=$(( $(date +%s)-t1 ))" >> "$O/units.txt"
[ $rc -eq 0 ] && touch "$O/$d/done" || echo "$d GRID_FAIL rc=$rc" >> "$O/fail.txt"
exit $rc
