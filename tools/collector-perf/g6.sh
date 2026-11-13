#!/bin/bash
# G6: старый и новый бинарь бок о бок 1 ч на подмножествах шардов всех четырёх бирж одновременно. Результаты — /data/perf/g6-<биржа>.txt
cd /data/perf
W=${1:-3600}
./shadow.sh binance $W book00,book08,trade00,mark00 arms-g6-binance.txt > g6-binance.txt 2>&1 &
./shadow.sh bybit $W book00,book04,trade00,mark00 arms-g6.txt > g6-bybit.txt 2>&1 &
./shadow.sh okx $W book00,book02,trade00,mark00 arms-g6.txt > g6-okx.txt 2>&1 &
./shadow.sh bingx $W book00,trade00 arms-g6.txt > g6-bingx.txt 2>&1 &
wait
echo ALLDONE > g6.done
