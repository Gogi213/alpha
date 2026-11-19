#!/usr/bin/env bash
# G-022 turbo: Rust load ladder on Binance — the same method as TK-531 (`tools/collector/ladder.py`): ONE connection per window, fast path only (RTT <= 300 ms),
# 180 s window, first 5 s after the subscribe dropped, kinds book / agg / mark, groups top (by quoteVolume24h) / mid (from rank 150), steps 10..320 and the full pool 522 (top),
# 3 windows per step. Units (kind x group) run in parallel, each unit walks its steps one after another; the 3 repeats of a step run at the same time (time budget).
#   rladder.sh <tag> <alpha binary>        -> /data/turbo-probe/<tag>/<kind>-<group>-n<N>-r<R>.json, DONE
# Stop soak-binance first. Background unit (systemd-run), never in the ssh session (В-147).
set -u
TAG="$1"; BIN="$2"
D=/data/turbo-probe/$TAG; mkdir -p "$D"
PY=/root/bnws-venv/bin/python; SRC=/data/turbo-probe/src/tools/collector
[ -s "$D/symbols.json" ] || $PY -c "
import json, sys
sys.path.insert(0, '$SRC')
import ladder
json.dump(ladder.symbols('binance'), open('$D/symbols.json', 'w'))"
unit() {  # kind group steps...
  local K="$1" G="$2"; shift 2
  for N in "$@"; do
    for R in 1 2 3; do
      "$BIN" collect ladder --kind "$K" --n "$N" --group "$G" --dur 180 --symbols-file "$D/symbols.json" --out "$D/$K-$G-n$N-r$R.json" > "$D/$K-$G-n$N-r$R.log" 2>&1 &
    done
    wait
    sleep 10
  done
}
for K in book agg mark; do
  unit "$K" top 10 20 40 80 160 320 522 &
  unit "$K" mid 10 20 40 80 160 320 &
done
wait
echo done > "$D/DONE"
