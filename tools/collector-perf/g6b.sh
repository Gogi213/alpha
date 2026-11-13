#!/bin/bash
# G6b: финальный кандидат против прежнего бинаря бок о бок 60 мин, по 2 шарда на биржу, 4 биржи одновременно; больше на srv ничего. Результаты — /data/perf/g6b-<биржа>.txt; монитор боя — g6b-mon.txt
cd /data/perf
W=${1:-3600}
NEWFLAGS=${NEWFLAGS:-"--reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel"}
NEWBIN=${NEWBIN:-/data/perf/alpha-final2}
rm -f g6b.done g6b-mon.txt
for e in binance bybit okx bingx; do
  extra=""; [ $e = binance ] && extra="--never-slow --lazy"
  printf "A-old|/data/perf/alpha-base3|%s\nNEW|%s|%s %s\n" "$extra" "$NEWBIN" "$extra" "$NEWFLAGS" > arms-g6b-$e.txt
done
nohup ./g6b-mon.sh > /dev/null 2>&1 &
./shadow.sh binance $W book00,mark00 arms-g6b-binance.txt > g6b-binance.txt 2>&1 &
./shadow.sh bybit $W book00,trade00 arms-g6b-bybit.txt > g6b-bybit.txt 2>&1 &
./shadow.sh okx $W book00,trade00 arms-g6b-okx.txt > g6b-okx.txt 2>&1 &
./shadow.sh bingx $W book00,trade00 arms-g6b-bingx.txt > g6b-bingx.txt 2>&1 &
wait
[ -f g6b.done ] || echo DONE > g6b.done
