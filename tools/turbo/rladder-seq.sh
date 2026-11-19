#!/usr/bin/env bash
# G-022 turbo: Rust load ladder with the SAME concurrency as the Python ladder of TK-531: 6 units (kind x group) side by side, each unit walks its repeats one after another
# (repeat 1: all its steps, then repeat 2, then 3), one connection per unit at a time, 20 s pause between windows (ladder.sh). At most 6 connections at once (owner 13.11).
#   rladder-seq.sh <tag> <alpha binary> "<steps top>" "<steps mid>"      -> /data/turbo-probe/<tag>/<kind>-<group>-n<N>-r<R>.json, DONE
# Nothing else may run on srv meanwhile (no builds, no tests). Background unit (systemd-run), never in the ssh session (В-147).
set -u
TAG="$1"; BIN="$2"; STEPS_TOP="$3"; STEPS_MID="$4"
D=/data/turbo-probe/$TAG; mkdir -p "$D"
[ -s "$D/symbols.json" ] || cp /data/turbo-probe/l1/symbols.json "$D/symbols.json"
unit() {  # kind group steps
  local K="$1" G="$2" S="$3"
  for R in 1 2 3; do
    for N in $S; do
      "$BIN" collect ladder --kind "$K" --n "$N" --group "$G" --dur 180 --symbols-file "$D/symbols.json" --out "$D/$K-$G-n$N-r$R.json" > "$D/$K-$G-n$N-r$R.log" 2>&1
      sleep 20
    done
  done
}
for K in book agg mark; do
  unit "$K" top "$STEPS_TOP" &
  unit "$K" mid "$STEPS_MID" &
done
wait
echo done > "$D/DONE"
