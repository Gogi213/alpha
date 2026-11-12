#!/usr/bin/env bash
# G-022 turbo: cold-start window of ONE exchange — a fresh process, an empty root, 3 minutes counted from the process start (not from a settled state).
#   coldstart.sh <binance|bybit> <tag> <alpha binary> [extra `collect run` args]
# Nothing else may dial this exchange from this IP meanwhile (stop the soak unit first and wait 5 min — the exchange counts dials per IP).
# Result in /data/turbo-probe/<tag>/: data/ (files + events-*.jsonl + stats-*), coverage.json (time to all shards covered, dial budget events),
# restcheck.json (REST oracle over [process start, +180 s]), zstd-test.txt, DONE.
# Run it as a background unit (systemd-run), never in the ssh session (В-147).
set -u
EX="$1"; TAG="$2"; BIN="$3"; shift 3
D=/data/turbo-probe/$TAG; mkdir -p "$D"
SRC=/data/turbo-probe/src/tools; PY=/root/bnws-venv/bin/python
UNIT="cold-$TAG"
systemctl stop "$UNIT.service" 2>/dev/null
systemd-run --quiet --unit="$UNIT" --collect -p KillMode=mixed -p TimeoutStopSec=60 -p LimitNOFILE=65536 \
  "$BIN" collect run --exchange "$EX" --root "$D/data" --health "$D/health.json" --lanes 2 --duration-s 185 "$@"
sleep 8
T0=$("$PY" -c "import glob,json,sys; print(json.load(open(sorted(glob.glob('$D/data/pool-*.json'))[0]))['ts_ms'])")
echo "$T0" > "$D/t0-ms.txt"
while systemctl is-active --quiet "$UNIT.service"; do sleep 2; done
T1=$((T0 + 180000))
# the REST oracle right after the window: Bybit recent-trade keeps only the last 1000 trades per symbol
"$PY" "$SRC/turbo/coverage.py" "$D/data" --window-s 180 --out "$D/coverage.json" > "$D/coverage.log" 2>&1
TC=$("$PY" -c "import json; c=json.load(open('$D/coverage.json')); m=c['first_connected_s']['max']; print($T0 + int(((m if m is not None else 180) + 3) * 1000))")
echo "$TC" > "$D/covered-ms.txt"
"$PY" "$SRC/turbo/restcheck.py" "$EX" "$D/data" "$T0" "$T1" --symbols 25 --spread --covered-ms "$TC" --out "$D/restcheck.json" > "$D/restcheck.log" 2>&1
for f in "$D"/data/20*/*.tsv.zst; do
  echo "$(zstd -tq "$f" >/dev/null 2>&1 && echo ok || echo BAD) ok-file=$([ -f "$f.ok" ] && cat "$f.ok" | tr -d '\n' || echo none) $(basename "$f")"
done > "$D/zstd-test.txt"
journalctl -u "$UNIT.service" --no-pager -n 30 > "$D/unit.log" 2>&1
echo done > "$D/DONE"
