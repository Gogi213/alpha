#!/usr/bin/env bash
# G-022 turbo: one ladder step repeated ALONE — Rust probe and Python ladder.py side by side (2 connections, same minutes), 3 rounds x 180 s.
#   ladder160.sh <tag> <alpha binary> <kind> <group> <streams> [rust-only|both]
# Waits for the running Rust ladder (DONE of tag l1) so that nothing else dials meanwhile. Output /data/turbo-probe/<tag>/{rust,py}-r<R>.json, DONE.
set -u
TAG="$1"; BIN="$2"; K="$3"; G="$4"; N="$5"; MODE="${6:-both}"
D=/data/turbo-probe/$TAG; mkdir -p "$D"
PY=/root/bnws-venv/bin/python; SRC=/data/turbo-probe/src/tools/collector
while [ ! -f /data/turbo-probe/l1/DONE ]; do sleep 3; done
sleep 5
for R in 1 2 3; do
  "$BIN" collect ladder --kind "$K" --n "$N" --group "$G" --dur 180 --symbols-file /data/turbo-probe/l1/symbols.json --out "$D/rust-r$R.json" > "$D/rust-r$R.log" 2>&1 &
  [ "$MODE" = both ] && "$PY" "$SRC/ladder.py" "$K" "$N" "$G" 180 "$D/py-r$R.json" binance > "$D/py-r$R.log" 2>&1 &
  wait
  sleep 10
done
echo done > "$D/DONE"
