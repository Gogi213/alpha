#!/bin/bash
# Выдержка rx: четыре биржи, две руки (одна сборка: выдержка 0 и 300 мс), бок о бок. hold-shadow.sh <бинарь> <секунд>; сторож рук — armwatch.py → /data/perf/armwatch.tsv
cd /data/perf
BIN=${1:-/data/perf/alpha-final6b}; W=${2:-900}
for e in binance bybit okx bingx; do
  extra=""; wr=10000; sh=book00,trade00
  if [ $e = binance ]; then extra="--never-slow --lazy"; wr=5000; sh=book00,book07,mark00; fi
  printf "H0|%s|%s --reactor --worker-inline --reactor-window-us %s --rx-stamp kernel\nH300|%s|%s --reactor --worker-inline --reactor-window-us %s --rx-stamp kernel --rx-hold-ms 300\n" "$BIN" "$extra" "$wr" "$BIN" "$extra" "$wr" > arms-hold-$e.txt
done
python3 armwatch.py $W binance bybit okx bingx &
PIDS=($!)
for e in binance bybit okx bingx; do
  sh=book00,trade00; [ $e = binance ] && sh=book00,book07,mark00
  ./shadow.sh $e $W $sh arms-hold-$e.txt > hold-$e.txt 2>&1 & PIDS+=($!)
done
wait "${PIDS[@]}"
echo DONE > hold-shadow.done
