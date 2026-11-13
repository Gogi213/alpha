#!/bin/bash
# G6b: сравнение выводов старой и новой руки после остановки (`collect compare`), здоровье рук. Запуск на srv: g6b-compare.sh <тег каталога sh/<биржа>-<тег>>; итог — /data/perf/g6b-compare-<биржа>.txt
cd /data/perf
TAG=${1:?тег}
BIN=${BIN:-/data/perf/alpha-final4}
for e in binance bybit okx bingx; do
  d=sh/$e-$TAG
  {
    echo "--- $e: A-old vs NEW"
    $BIN collect compare --a $d/A-old/data --b $d/NEW/data
    for arm in A-old NEW; do
      python3 - $d/$arm/health.json $arm <<'PY'
import json, sys
h = json.load(open(sys.argv[1]))
t = h["totals"]
print(sys.argv[2], "totals", t, "| ws", {k: h["ws"][k] for k in ("gaps_24h", "reconnects_24h", "timeouts_24h")}, "| ring", h["backpressure"]["ring"], "| sink", h["backpressure"]["sink"], "| queue_lag", h["backpressure"]["queue_lag_ms"])
PY
    done
  } > g6b-compare-$e.txt 2>&1
done
echo ALLDONE > g6b-compare.done
