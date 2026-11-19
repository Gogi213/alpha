#!/bin/bash
# Сравнение выводов G6 (старый против новых рук) после остановки: бинарь с `collect compare`; низкий приоритет. Итог — /data/perf/g6-compare-<биржа>.txt
cd /data/perf
BIN=${1:-/data/perf/alpha-new6}
for d in sh/binance-162441 sh/bybit-162441 sh/okx-162441 sh/bingx-162441; do
  e=$(basename $d | cut -d- -f1)
  {
    for arm in N2 N5; do
      echo "--- $e: A-old vs $arm"
      $BIN collect compare --a $d/A-old/data --b $d/$arm/data
    done
    for arm in A-old N2 N5; do
      python3 - $d/$arm/health.json $arm <<'PY'
import json, sys
h = json.load(open(sys.argv[1]))
t = h["totals"]
print(sys.argv[2], "totals", t, "| ws", {k: h["ws"][k] for k in ("gaps_24h", "reconnects_24h", "timeouts_24h")}, "| ring", h["backpressure"]["ring"], "| sink", h["backpressure"]["sink"])
PY
    done
  } > g6-compare-$e.txt 2>&1
done
echo ALLDONE > g6-compare.done
