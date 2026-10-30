#!/bin/bash
# TK-458 B2 G6.4: события по монетам (xargs -P15) + агрегатор. Метка /data/tk458/DONE
PY=/data/tk421/venv/bin/python; D=/srv/alpha-sda/candles-1m/binance-um; O=/data/tk458/ev; mkdir -p $O
ls $D | grep "USDT.csv.gz$" | sed "s/.csv.gz//" | xargs -P 15 -I{} $PY -I /data/tk0451/bp_g6.py $D $O/{} {} > /data/tk458/run.log 2>&1
$PY -I /data/tk0451/bp_g6_agg.py $O /data/tk458/g6 > /data/tk458/agg.log 2>&1
touch /data/tk458/DONE
