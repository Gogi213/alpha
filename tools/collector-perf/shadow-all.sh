#!/bin/bash
# Бок о бок по трём биржам подряд (Binance, Bybit, BingX); OKX снят отдельно. Результаты — /data/perf/sh-<биржа>.txt
cd /data/perf
./shadow.sh binance 180 book00,book08,trade00,mark00 arms-binance.txt > sh-binance.txt 2>&1
./shadow.sh bybit 180 book00,book04,trade00,mark00 arms-plain.txt > sh-bybit.txt 2>&1
./shadow.sh bingx 180 book00,trade00 arms-plain.txt > sh-bingx.txt 2>&1
echo ALLDONE > sh-all.done
