#!/usr/bin/env bash
# G-022 turbo: step ladder of the FULL collector on Binance — ONE process at a time (owner 13.11: «просто проводим тестирование по плану. от меньшего к большему как шли»).
#   steps.sh <tag> <binary> "<flags>" "<steps: N or full ...>" [<windows per step, default 3>]
# A step = top-N symbols of the pool by quoteVolume24h (`full` = the whole pool), 3 windows x 3 min, each window a fresh process (`collect run --lanes 2`, budget <= 280 within the exchange's
# 300 / 5 min), 20 s pause between windows. Per window: coverage.py, abwin.py (events, book lag, late, mark coverage), restcheck.py (REST trades), CPU (cgroup) -> stepsum.py verdict line.
# status.txt: one line per window and `STEP <N> OK|FAIL`; stops at the first not clean window (STOPPED line, exit 1). Needs /data/turbo-probe/l1/symbols.json (quoteVolume ranking)
# and /data/turbo-probe/ab1/pool.json (the pool). Background unit (systemd-run), never in the ssh session (В-147); nothing else may run on srv meanwhile.
set -u -o pipefail
TAG="$1"; BIN="$2"; FLAGS="$3"; STEPS="$4"; NWIN="${5:-3}"
D=/data/turbo-probe/$TAG; mkdir -p "$D"
PY=/root/bnws-venv/bin/python; S=/data/turbo-probe/src/tools/turbo
ST="$D/status.txt"
[ -s "$D/pool-full.json" ] || cp /data/turbo-probe/ab1/pool.json "$D/pool-full.json"
$PY "$S/steppools.py" "$D"
budget() {  # connect budget of the next process: <= 280 and the exchange's 300 per 5 min minus our dials of the last 5 min (earlier windows)
  local B
  while :; do
    B=$($PY "$S/stepbudget.py" "$D")
    [ "$B" -ge 120 ] && { echo "$B"; return; }
    sleep 10
  done
}
window() {  # label npool
  local L="$1" NP="$2" W="$D/$1" BUD T0 U0 U1 T1 TC NS
  local CG="/sys/fs/cgroup/system.slice/st-$TAG-$1.service/cpu.stat"
  rm -rf "$W"; mkdir -p "$W"
  BUD=$(budget)
  # shellcheck disable=SC2086
  systemd-run --quiet --unit="st-$TAG-$L" --collect -p KillMode=mixed -p TimeoutStopSec=60 -p LimitNOFILE=65536 \
    "$BIN" collect run --exchange binance --root "$W/data" --health "$W/health.json" --lanes 2 --pool-file "$D/pool-$NP.json" --connect-budget "$BUD" --duration-s 186 $FLAGS
  sleep 6
  T0=$($PY -c "import glob,json; print(json.load(open(glob.glob('$W/data/pool-*.json')[0]))['ts_ms'])")
  echo "$T0" > "$W/t0-ms.txt"
  while [ "$(date +%s%3N)" -lt $(( T0 + 10000 )) ]; do sleep 1; done
  U0=$(awk '/usage_usec/{print $2}' "$CG" 2>/dev/null || echo 0)
  while [ "$(date +%s%3N)" -lt $(( T0 + 175000 )) ]; do sleep 1; done
  U1=$(awk '/usage_usec/{print $2}' "$CG" 2>/dev/null || echo 0)
  echo "{\"cpu_pct\": $(( (U1 - U0) / 1650000 ))}" > "$W/cpu.json"   # percent of one core over 165 s
  while systemctl is-active --quiet "st-$TAG-$L.service"; do sleep 2; done
  T1=$(( T0 + 180000 ))
  $PY "$S/coverage.py" "$W/data" --window-s 180 --out "$W/cov.json" > "$W/cov.log" 2>&1
  TC=$($PY -c "import json; c=json.load(open('$W/cov.json')); m=c['first_connected_s']['max']; print($T0 + int(((m if m is not None else 180) + 3) * 1000))")
  $PY "$S/abwin.py" --run "X=$W/data" --t0 "$T0" --t1 "$T1" --pool "$D/pool-$NP.json" --covered-ms "$TC" --out "$W/win.json" > "$W/win.log" 2>&1
  NS=25; [ "$NP" != full ] && [ "$NP" -lt 25 ] && NS="$NP"
  $PY "$S/restcheck.py" binance "$W/data" "$T0" "$T1" --symbols "$NS" --spread --covered-ms "$TC" --out "$W/rest.json" > "$W/rest.log" 2>&1
  $PY "$S/stepsum.py" "$W" "$L" | tee -a "$ST"
}
for NP in $STEPS; do
  for K in $(seq 1 "$NWIN"); do
    window "$TAG-n$NP-w$K" "$NP" || { echo "STEP $NP FAIL (window $K)" >> "$ST"; exit 1; }
    sleep 20
  done
  echo "STEP $NP OK" >> "$ST"
done
echo "ALLDONE" >> "$ST"
