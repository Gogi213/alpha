#!/bin/bash
# TK-462 шаг 4б: X1/X2 по 84 монетам × ТФ 60/240 (NX=15); метка S4BDONE
cd /data/tk0455; OUT=/data/tk0462/s4b; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um
rm -f $OUT/S4BDONE
for tf in 240 60; do for f in $(ls $SRC/*USDT.csv.gz); do echo "$(basename $f .csv.gz) $tf"; done; done > $OUT/plan.txt
job() { [ -s $OUT/$1-$2.csv.gz ] && return 0; $PY tk462_step4b.py $SRC/$1.csv.gz $2 /data/tk0455/fund $OUT/$1-$2.tmp.csv.gz 2>>$OUT/err.log && mv $OUT/$1-$2.tmp.csv.gz $OUT/$1-$2.csv.gz || echo FAIL $1 $2 >>$OUT/err.log; }
export -f job; export OUT PY SRC
xargs -P ${NX:-15} -L1 bash -c 'job $0 $1' < $OUT/plan.txt
echo "$(ls $OUT/*.csv.gz | wc -l) fail $(grep -c FAIL $OUT/err.log)" > $OUT/S4BDONE
