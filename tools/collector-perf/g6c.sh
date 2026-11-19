#!/bin/bash
# G6c: финальный кандидат (final5) против прежнего бинаря, 4 биржи, 20 мин (проверка дельты «без сна окна при полном буфере»), затем контроль BingX «старый против старого» 10 мин.
cd /data/perf
NEWBIN=${NEWBIN:-/data/perf/alpha-final5} ./g6b.sh 1200
printf "A-old|/data/perf/alpha-base3|\nA-old2|/data/perf/alpha-base3|\n" > arms-ctl-bingx.txt
./shadow.sh bingx 600 book00,trade00 arms-ctl-bingx.txt > g6c-ctl-bingx.txt 2>&1
echo DONE > g6c.done
