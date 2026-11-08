#!/bin/bash
# TK-500: orch.py --validity: DOGE Binance x Gate (DOGE_USDT x10) 29.09 (Gate: дыра, часовые снимки) с validity.csv.gz G0 -> <пара>.holes, колонка trunc в exits, доли причин; чанк 2 суток 28-29.09.
cd /root/p40core || exit 1
printf 'asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB,ctA,ctB\n1,5,binance,DOGEUSDT,,,,gate,DOGE_USDT,10,,,1,1\n' > pairs_doge_gate.csv
rm -rf /tmp/orchh
python3 orch.py pairs_doge_gate.csv /tmp/orchh --days 2026-09-28,2026-09-29 --chunk 2 --jobs 1 --validity validity.csv.gz 2>&1 | tail -3
D=/tmp/orchh/1/2026-09-28+2
wc -l $D/5.holes; head -3 $D/5.holes; head -1 $D/5.exits.csv
awk -F, 'NR==1{for(i=1;i<=NF;i++)if($i=="trunc")c=i;next}{n[$c]++}END{for(k in n)print "trunc",k,n[k]}' $D/5.exits.csv
