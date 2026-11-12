#!/usr/bin/env bash
# G-022 turbo: 3-minute window of the Rust collector alone (no Python unit to compare): CPU / CPU wait / Recv-Q (winmon), acceptance (accept_ws.py),
# file checks (check_files.py), REST completeness of Binance trades (restcheck.py) — all on the same 180 s.
#   probe_rust.sh <binance|bybit> <tag> <alpha> [extra collect args]
set -u
EX="$1"; TAG="$2"; BIN="$3"; shift 3
D=/data/turbo-probe/$TAG; mkdir -p "$D"; rm -rf "$D/data"
SRC=/data/turbo-probe/src/tools; PY=/root/bnws-venv/bin/python; UNIT="turbo-$TAG"
systemctl stop "$UNIT.service" 2>/dev/null
systemd-run --quiet --unit="$UNIT" --collect -p KillMode=mixed -p TimeoutStopSec=60 -p LimitNOFILE=65536 \
  "$BIN" collect run --exchange "$EX" --root "$D/data" --health "$D/health.json" --lanes 2 --duration-s 300 "$@"
sleep 70
T0=$(date +%s%3N)
"$PY" "$SRC/turbo/winmon.py" --seconds 180 --unit "$UNIT.service" --out "$D/winmon.json" > "$D/winmon.log" 2>&1
T1=$(date +%s%3N)
echo "$T0 $T1" > "$D/window-ms.txt"
cp "$D/health.json" "$D/health-window.json"
(cd "$SRC/collector" && HEALTH="$D/health.json" "$PY" accept_ws.py --exchange "$EX" --root "$D/data" --since-ms "$T0" --until-ms "$T1" --out "$D/accept.json" > "$D/accept.log" 2>&1)
while systemctl is-active --quiet "$UNIT.service"; do sleep 2; done
"$PY" "$SRC/turbo/check_files.py" "$D/data" --exchange "$EX" --out "$D/check.json" > "$D/check.log" 2>&1
[ "$EX" = binance ] && "$PY" "$SRC/turbo/restcheck.py" binance "$D/data" "$T0" "$T1" --symbols 25 --out "$D/restcheck.json" > "$D/restcheck.log" 2>&1
journalctl -u "$UNIT.service" --no-pager -n 20 > "$D/unit.log" 2>&1
echo done > "$D/DONE"
