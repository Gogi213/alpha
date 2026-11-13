#!/bin/bash
# Монитор боя во время G6b: раз в 30 с queue_lag p99 четырёх боевых коллекторов и PSI cpu; при queue_lag p99 > 10 мс два замера подряд на любой бирже — стоп всех теневых процессов.
LOG=/data/perf/g6b-mon.txt
BAD=0
while [ ! -f /data/perf/g6b.done ]; do
  OUT=$(python3 - <<PY
import json
r = []
mx = 0.0
for e, p in (("binance", "/data/turbo-probe/soak-binance/health.json"), ("bybit", "/data/turbo-probe/soak-bybit/health.json"), ("okx", "/data/collect/okx/health.json"), ("bingx", "/data/collect/bingx/health.json")):
    try:
        h = json.load(open(p))
        q = h["backpressure"]["queue_lag_ms"]["p99"]
        r.append(f"{e}={q}")
        mx = max(mx, q or 0)
    except Exception as ex:
        r.append(f"{e}=?")
print(" ".join(r), "max", mx)
PY
)
  PSI=$(grep some /proc/pressure/cpu | sed 's/ total.*//')
  echo "$(date -u +%T) $OUT | $PSI | load $(cut -d' ' -f1 /proc/loadavg)" >> $LOG
  MAXV=$(echo "$OUT" | awk '{print $NF}')
  if awk "BEGIN{exit !($MAXV > 10)}"; then BAD=$((BAD+1)); else BAD=0; fi
  if [ $BAD -ge 2 ]; then
    echo "$(date -u +%T) ABORT: production queue_lag p99 $MAXV > 10 ms twice" >> $LOG
    for u in $(systemctl list-units "shadow-*" --no-legend | awk '{print $1}'); do systemctl stop $u; done
    echo ABORTED > /data/perf/g6b.done
    exit 1
  fi
  sleep 30
done
