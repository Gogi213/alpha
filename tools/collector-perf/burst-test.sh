#!/bin/bash
# Залп после подписки (BingX: снимки 179 потоков на соединение): queue_lag p99 рук каждые 10 с с момента запуска. burst-test.sh <биржа> <секунд> <шарды> <файл рук>; итог — /data/perf/burst-<биржа>.tsv
cd /data/perf
EX=$1; WIN=$2; SHARDS=$3; ARMS=$4
rm -f burst-$EX.tsv
./shadow.sh $EX $WIN $SHARDS $ARMS > burst-$EX.shadow.txt 2>&1 &
sleep 8
D=$(ls -td sh/$EX-* | head -1)
T0=$(date +%s)
while [ $(( $(date +%s) - T0 )) -lt $WIN ]; do
  for a in $(ls $D); do
    [ -f $D/$a/health.json ] || continue
    python3 - $D/$a/health.json $a $(( $(date +%s) - T0 )) >> burst-$EX.tsv <<'PY'
import json, sys
h = json.load(open(sys.argv[1]))
q = h["backpressure"]["queue_lag_ms"]
r = h.get("reactor") or {}
print(sys.argv[3], sys.argv[2], q.get("p50"), q.get("p99"), q.get("p99_9"), h["lag_out_ms"].get("p99"), r.get("catchup_loops", ""), sep="\t")
PY
  done
  sleep 10
done
wait
