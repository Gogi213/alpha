#!/bin/bash
# TK-458 B2 G3.1: события по монетам (xargs -P15) + агрегатор. Метка /data/tk458/DONE8
PY=/data/tk421/venv/bin/python; D=/srv/alpha-sda/candles-1m/binance-um; O=/data/tk458/ev8; mkdir -p $O
ls $D | grep "USDT.csv.gz$" | sed "s/.csv.gz//" | xargs -P 15 -I{} $PY -I /data/tk0451/bp_g3.py $D $O/{} {} > /data/tk458/run8.log 2>&1
$PY -I /data/tk0451/bp_g3_agg.py $O /data/tk458/g3 > /data/tk458/agg8.log 2>&1
touch /data/tk458/DONE8
