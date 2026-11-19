#!/usr/bin/env bash
# G-022 turbo: REST oracle over a 3-minute window of the RUNNING soak (steady state), run at the window end (Bybit recent-trade keeps only the last 1000 trades).
#   steady.sh <tag> <window start "YYYY-MM-DD HH:MM:SS" UTC> <min run epoch of the soak process>
# Result in /data/turbo-probe/<tag>/: restcheck-binance.json, restcheck-bybit.json, coverage-*.json (per-shard seconds without a live session since the soak start), DONE.
# Run as a background unit (systemd-run), never in the ssh session (В-147).
set -u
TAG="$1"; START="$2"; MINRUN="$3"
PY=/root/bnws-venv/bin/python; S=/data/turbo-probe/src/tools/turbo; D=/data/turbo-probe/$TAG; mkdir -p "$D"
T0=$(( $(date -d "$START UTC" +%s) * 1000 )); T1=$(( T0 + 180000 ))
while [ "$(date +%s)" -lt $(( T1 / 1000 + 8 )) ]; do sleep 2; done
$PY $S/restcheck.py bybit /data/turbo-probe/soak-bybit/data "$T0" "$T1" --symbols 25 --spread --min-run "$MINRUN" --out "$D/restcheck-bybit.json" > "$D/rb.log" 2>&1 &
$PY $S/restcheck.py binance /data/turbo-probe/soak-binance/data "$T0" "$T1" --symbols 25 --spread --min-run "$MINRUN" --out "$D/restcheck-binance.json" > "$D/rn.log" 2>&1
wait
for e in binance bybit; do
  $PY $S/coverage.py /data/turbo-probe/soak-$e/data --window-s 20000 --out "$D/coverage-$e.json" > "$D/coverage-$e.log" 2>&1
  cp /data/turbo-probe/soak-$e/health.json "$D/health-$e.json" 2>/dev/null
done
echo done > "$D/DONE"
