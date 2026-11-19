#!/usr/bin/env bash
# G-022 turbo: continuous soak of the latest working build on srv (В-147: long evidence accumulates, nobody waits for it).
#   soak.sh start <alpha binary>   — stop the running soak gracefully (one SIGTERM, files closed) and start both exchanges with this binary
#   soak.sh stop | status
# Units soak-binance / soak-bybit (Restart=always), full pools, data /data/turbo-probe/soak-<exchange>/data, health /data/turbo-probe/soak-<exchange>/health.json,
# the binary is copied to /data/turbo-probe/soak-bin/alpha-<md5> so a restart never depends on a file being overwritten. No capture (diagnostics only, ≈ 50 GB/day).
set -u
D=/data/turbo-probe
case "${1:-status}" in
  start)
    BIN="$2"; MD5=$(md5sum "$BIN" | cut -c1-8); mkdir -p "$D/soak-bin"; cp -f "$BIN" "$D/soak-bin/alpha-$MD5"; chmod +x "$D/soak-bin/alpha-$MD5"
    for EX in binance bybit; do
      systemctl stop "soak-$EX.service" 2>/dev/null; mkdir -p "$D/soak-$EX"
      systemd-run --quiet --unit="soak-$EX" --collect -p Restart=always -p RestartSec=10 -p KillMode=mixed -p TimeoutStopSec=60 -p LimitNOFILE=65536 \
        "$D/soak-bin/alpha-$MD5" collect run --exchange "$EX" --root "$D/soak-$EX/data" --health "$D/soak-$EX/health.json" --lanes 2
      echo "$(date -u +%FT%TZ) $EX start alpha-$MD5" >> "$D/soak-$EX/starts.log"
    done ;;
  stop) for EX in binance bybit; do systemctl stop "soak-$EX.service"; done ;;
  *) for EX in binance bybit; do systemctl is-active "soak-$EX.service"; tail -2 "$D/soak-$EX/starts.log" 2>/dev/null; done ;;
esac
