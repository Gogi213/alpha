#!/usr/bin/env bash
# G-022 turbo: N consecutive 3-minute windows of one or two concurrent Binance collector processes, same pool, same script per window.
#   abrun.sh <tag> <windows> <alpha binary> <label>=<connect budget>[:extra collect args] [<label>=…]
#   e.g. abrun.sh ab1 5 /data/turbo-probe/soak-bin/alpha-XXXX "A=140:--replace-slow-as-is" "B=140:--no-slow-replace --lazy-replace"
# Window k = [process start + 180 s * (k-1), + 180 s); window 1 is the cold start. Per window: abwin.py (events, disk lag, coverage) and restcheck.py (REST aggTrades,
# 25 symbols evenly over the ranking) for each run. Results /data/turbo-probe/<tag>/win<k>.json and rest-<label>-<k>.json; table: abtable.py <tag dir>.
# Stop soak-binance BEFORE (and wait 5 min: the exchange counts dials per IP); restart it after. Run as a background unit (systemd-run), never in the ssh session (В-147).
set -u
TAG="$1"; NWIN="$2"; BIN="$3"; shift 3
D=/data/turbo-probe/$TAG; mkdir -p "$D"
SRC=/data/turbo-probe/src/tools/turbo; PY=/root/bnws-venv/bin/python
POOL=$D/pool.json
cp "$(ls -t /data/turbo-probe/soak-binance/data/pool-*.json | head -1)" "$POOL"
DUR=$(( NWIN * 180 + 20 ))
LABELS=""
for SPEC in "$@"; do
  L="${SPEC%%=*}"; REST="${SPEC#*=}"; BUD="${REST%%:*}"; EXTRA=""; [ "$REST" != "$BUD" ] && EXTRA="${REST#*:}"
  LABELS="$LABELS $L"; rm -rf "$D/$L"; mkdir -p "$D/$L"
  echo "$L budget=$BUD extra=$EXTRA" >> "$D/runs.txt"
  # shellcheck disable=SC2086
  systemd-run --quiet --unit="ab-$TAG-$L" --collect -p KillMode=mixed -p TimeoutStopSec=60 -p LimitNOFILE=65536 \
    "$BIN" collect run --exchange binance --root "$D/$L/data" --health "$D/$L/health.json" --lanes 2 --pool-file "$POOL" --connect-budget "$BUD" --duration-s "$DUR" $EXTRA
done
sleep 6
T0=$("$PY" -c "import glob,json; print(min(json.load(open(f))['ts_ms'] for f in glob.glob('$D/*/data/pool-*.json')))")
echo "$T0" > "$D/t0-ms.txt"
for K in $(seq 1 "$NWIN"); do
  W0=$(( T0 + (K - 1) * 180000 )); W1=$(( W0 + 180000 ))
  while [ "$(date +%s%3N)" -lt $(( W1 + 12000 )) ]; do sleep 1; done
  RUNS=(); for L in $LABELS; do RUNS+=(--run "$L=$D/$L/data"); done
  (
    "$PY" "$SRC/abwin.py" "${RUNS[@]}" --t0 "$W0" --t1 "$W1" --out "$D/win$K.json" > "$D/win$K.log" 2>&1
    for L in $LABELS; do
      COV=()
      if [ "$K" = 1 ]; then
        "$PY" "$SRC/coverage.py" "$D/$L/data" --window-s 180 --out "$D/cov-$L.json" > "$D/cov-$L.log" 2>&1
        TC=$("$PY" -c "import json; c=json.load(open('$D/cov-$L.json')); m=c['first_connected_s']['max']; print($T0 + int(((m if m is not None else 180) + 3) * 1000))")
        COV=(--covered-ms "$TC")
      fi
      "$PY" "$SRC/restcheck.py" binance "$D/$L/data" "$W0" "$W1" --symbols 25 --spread "${COV[@]}" --out "$D/rest-$L-$K.json" > "$D/rest-$L-$K.log" 2>&1
    done
    touch "$D/win$K.done"
  ) &
done
wait
for L in $LABELS; do
  while systemctl is-active --quiet "ab-$TAG-$L.service"; do sleep 2; done
  for f in "$D/$L"/data/20*/*.tsv.zst; do
    echo "$(zstd -tq "$f" >/dev/null 2>&1 && echo ok || echo BAD) ok-file=$([ -f "$f.ok" ] && tr -d '\n' < "$f.ok" || echo none) $(basename "$f")"
  done > "$D/zstd-$L.txt"
  journalctl -u "ab-$TAG-$L.service" --no-pager -n 20 > "$D/unit-$L.log" 2>&1
done
echo done > "$D/DONE"
