#!/bin/bash
# Размер поднятия rx в боевом health: clampwatch.sh <биржа> <секунд> — раз в 30 с строка (время, n за 60 с, p50, p99, p99.9, max мкс; rx_clamped всего, записей всего) → /data/perf/clamp-<биржа>.tsv
EX=$1; SECS=${2:-600}
case $EX in
  binance) H=/data/turbo-probe/soak-binance/health.json ;;
  bybit) H=/data/turbo-probe/soak-bybit/health.json ;;
  okx) H=/data/collect/okx/health.json ;;
  bingx) H=/data/collect/bingx/health.json ;;
esac
OUT=/data/perf/clamp-$EX.tsv; : > $OUT
END=$(( $(date +%s) + SECS ))
while [ $(date +%s) -lt $END ]; do
  python3 - $H >> $OUT <<'PY'
import json, sys, time
h = json.load(open(sys.argv[1]))
c = h.get("rx_clamp_us") or {}
t = h["totals"]
print(time.strftime("%H:%M:%S", time.gmtime()), c.get("n"), c.get("p50"), c.get("p99"), c.get("p99_9"), c.get("max"), t["rx_clamped"], t["rows"], h["process"]["version"], sep="\t")
PY
  sleep 30
done
