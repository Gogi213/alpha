#!/bin/bash
# Чистое окно CPU финального кандидата (чтение по записям включено) по всем биржам подряд: итог /data/perf/ws-<биржа>.txt
cd /data/perf
./shadow.sh binance 180 book00,book08,trade00,mark00 arms-final-binance.txt > ws-binance.txt 2>&1
./shadow.sh bybit 180 book00,book04,trade00,mark00 arms-final-plain.txt > ws-bybit.txt 2>&1
./shadow.sh okx 180 book00,book02,trade00,mark00 arms-final-plain.txt > ws-okx.txt 2>&1
./shadow.sh bingx 180 book00,trade00 arms-final-plain.txt > ws-bingx.txt 2>&1
echo ALLDONE > ws-all.done
