#!/usr/bin/env bash
# G-022 turbo: step ladder of the FULL collector — ONE process at a time (owner 13.11: «просто проводим тестирование по плану. от меньшего к большему как шли»).
#   [EXCH=binance|bybit|okx|bingx] [ROOT=<run root, default /data/turbo-probe>] [TOOLS=<this directory on srv>] steps.sh <tag> <binary> "<flags>" "<steps: N or full ...>" [<windows per step, default 3>]
# A step = top-N symbols of the pool by 24h volume (`full` = the whole pool), 3 windows x 3 min, each window a fresh process (`collect run --lanes 2`; Binance: budget <= 280 within the exchange's
# 300 / 5 min, OKX: the built-in 2 connects per second), 20 s pause between windows. Per window: coverage.py, abwin.py (events, book lag, late, mark coverage), restcheck.py (REST trades), CPU (cgroup)
# -> stepsum.py verdict line. status.txt: one line per window and `STEP <N> OK|FAIL`; stops at the first not clean window (STOPPED line, exit 1).
# Pool: Binance — /data/turbo-probe/l1/symbols.json (quoteVolume ranking) and /data/turbo-probe/ab1/pool.json; other exchanges — the binary's own `--pool-only` run (pool-full.json with `qv`).
# RESTPASS=defer (preferences п. 23: nothing extraneous on srv during collection windows): the REST pass is NOT run between windows; the window line is posted without the REST field
# (stepsum.py --no-rest) and `restpass.sh` runs the REST checks of all windows after the ladder.
# BingX (EXCH=bingx): REST keeps no trade history (the last 1000 trades of a symbol), so the oracle cannot be deferred: right after the window end (T0 + 181 s, the window itself is over, the process only
# finishes its last 6 s) `bxsnap.py` takes a REST snapshot of every pool symbol (≈ 22 s for 892 symbols at 40 requests/s); `restcheck.py bingx` then compares it with the files offline (+ id continuity of the whole pool).
# "covered" for BingX = the latest `subscribed` event + 3 s (one subscribe message per topic: a shard of 200 topics is fully subscribed 4 s after its connection).
# Background unit (systemd-run), never in the ssh session (В-147); nothing else of the same exchange may run on srv meanwhile.
set -u -o pipefail
EXCH="${EXCH:-binance}"
[ "$EXCH" = bingx ] && COVERKEY=subscribed
TAG="$1"; BIN="$2"; FLAGS="$3"; STEPS="$4"; NWIN="${5:-3}"
D=${ROOT:-/data/turbo-probe}/$TAG; mkdir -p "$D"
PY=/root/bnws-venv/bin/python; S=${TOOLS:-/data/turbo-probe/src/tools/turbo}
ST="$D/status.txt"
if [ ! -s "$D/pool-full.json" ]; then
  if [ "$EXCH" = binance ]; then
    cp /data/turbo-probe/ab1/pool.json "$D/pool-full.json"
  else
    "$BIN" collect run --exchange "$EXCH" --root "$D/poolprep" --pool-only > "$D/poolprep.log" 2>&1
    cp "$D"/poolprep/pool-*.json "$D/pool-full.json"
  fi
fi
$PY "$S/steppools.py" "$D"
budget() {  # connect budget of the next process (Binance only): <= 280 and the exchange's 300 per 5 min minus our dials of the last 5 min (earlier windows)
  local B
  [ "$EXCH" = binance ] || { echo 0; return; }
  while :; do
    B=$($PY "$S/stepbudget.py" "$D")
    [ "$B" -ge 120 ] && { echo "$B"; return; }
    sleep 10
  done
}
window() {  # label npool
  local L="$1" NP="$2" W="$D/$1" BUD T0 U0 U1 T1 TC NS BUDARG=""
  local CG="/sys/fs/cgroup/system.slice/st-$TAG-$1.service/cpu.stat"
  rm -rf "$W"; mkdir -p "$W"
  BUD=$(budget)
  [ "$BUD" -gt 0 ] && BUDARG="--connect-budget $BUD"
  # shellcheck disable=SC2086
  systemd-run --quiet --unit="st-$TAG-$L" --collect -p KillMode=mixed -p TimeoutStopSec=60 -p LimitNOFILE=65536 \
    "$BIN" collect run --exchange "$EXCH" --root "$W/data" --health "$W/health.json" --lanes 2 --pool-file "$D/pool-$NP.json" $BUDARG --duration-s 186 $FLAGS
  sleep 6
  T0=$($PY -c "import glob,json; print(json.load(open(glob.glob('$W/data/pool-*.json')[0]))['ts_ms'])")
  echo "$T0" > "$W/t0-ms.txt"
  while [ "$(date +%s%3N)" -lt $(( T0 + 10000 )) ]; do sleep 1; done
  U0=$(awk '/usage_usec/{print $2}' "$CG" 2>/dev/null || echo 0)
  while [ "$(date +%s%3N)" -lt $(( T0 + 175000 )) ]; do sleep 1; done
  U1=$(awk '/usage_usec/{print $2}' "$CG" 2>/dev/null || echo 0)
  echo "{\"cpu_pct\": $(( (U1 - U0) / 1650000 ))}" > "$W/cpu.json"   # percent of one core over 165 s
  if [ "$EXCH" = bingx ]; then
    while [ "$(date +%s%3N)" -lt $(( T0 + 181000 )) ]; do sleep 0.5; done
    $PY "$S/bxsnap.py" "$D/pool-$NP.json" "$T0" "$(( T0 + 180000 ))" "$W/rest-snap.json.gz" > "$W/snap.log" 2>&1
  fi
  while systemctl is-active --quiet "st-$TAG-$L.service"; do sleep 2; done
  T1=$(( T0 + 180000 ))
  $PY "$S/coverage.py" "$W/data" --window-s 180 --out "$W/cov.json" > "$W/cov.log" 2>&1
  TC=$($PY -c "import json; c=json.load(open('$W/cov.json')); m=c['first_${COVERKEY:-connected}_s']['max']; print($T0 + int(((m if m is not None else 180) + 3) * 1000))")
  $PY "$S/abwin.py" --run "X=$W/data" --t0 "$T0" --t1 "$T1" --pool "$D/pool-$NP.json" --covered-ms "$TC" --out "$W/win.json" > "$W/win.log" 2>&1
  NS=25; [ "$NP" != full ] && [ "$NP" -lt 25 ] && NS="$NP"
  if [ "$EXCH" = bingx ]; then
    $PY "$S/restcheck.py" bingx "$W/data" "$T0" "$T1" --snap "$W/rest-snap.json.gz" --covered-ms "$TC" --out "$W/rest.json" > "$W/rest.log" 2>&1
    $PY "$S/stepsum.py" "$W" "$L" | tee -a "$ST"
  elif [ "${RESTPASS:-inline}" = defer ]; then
    echo "$TC $NS" > "$W/rest-args.txt"
    $PY "$S/stepsum.py" "$W" "$L" --no-rest | tee -a "$ST"
  else
    $PY "$S/restcheck.py" "$EXCH" "$W/data" "$T0" "$T1" --symbols "$NS" --spread --covered-ms "$TC" --out "$W/rest.json" > "$W/rest.log" 2>&1
    $PY "$S/stepsum.py" "$W" "$L" | tee -a "$ST"
  fi
}
for NP in $STEPS; do
  for K in $(seq 1 "$NWIN"); do
    window "$TAG-n$NP-w$K" "$NP" || { echo "STEP $NP FAIL (window $K)" >> "$ST"; exit 1; }
    sleep 20
  done
  echo "STEP $NP OK" >> "$ST"
done
echo "ALLDONE" >> "$ST"
