#!/bin/bash
# TK-458 B2 G6.1/G6.2: события по монетам (xargs -P15) + агрегатор. Метка /data/tk458/DONE13
PY=/data/tk421/venv/bin/python; D=/srv/alpha-sda/candles-1m/binance-um; O=/data/tk458/ev13; mkdir -p $O
ls $D | grep "USDT.csv.gz$" | sed "s/.csv.gz//" | xargs -P 15 -I{} $PY -I /data/tk0451/bp_g61.py $D $O/{} {} > /data/tk458/run13.log 2>&1
$PY -I /data/tk0451/bp_g61_agg.py $O /data/tk458/g61 > /data/tk458/agg13.log 2>&1
touch /data/tk458/DONE13
