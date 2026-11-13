#!/bin/bash
# G4 по трём остальным биржам подряд (Binance уже снят): итог в /data/perf/stamp-<биржа>.txt
cd /data/perf
for e in bybit okx bingx; do
  EXCH=$e ./stamp_test.sh /data/perf/cap/$e/data/capture-book00.tsv 150 /data/perf/alpha-new5 \
    "thr|--rx-stamp user" \
    "b5|--reactor --worker-inline --reactor-window-us 5000 --rx-stamp kernel" \
    "x2|--reactor --worker-inline --reactor-window-us 2000 --rx-stamp kernel --rx-exact" \
    "x5|--reactor --worker-inline --reactor-window-us 5000 --rx-stamp kernel --rx-exact" \
    "x10|--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --rx-exact" > stamp-$e.txt 2>&1
done
echo ALLDONE > stamp-all.done
