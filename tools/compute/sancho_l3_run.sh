#!/bin/bash
# TK-455 боевой счёт слоя 3: 84 монеты × ТФ 60/240/1440, якорь k=2, xargs -P NX, докачиваемый (готовое пропускает).
# Метка: $OUT/DONE; ошибки: $OUT/err.log; пик памяти на единицу: $OUT/rss.log
set -u
W=/data/tk0455; OUT=$W/run; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um
mkdir -p "$OUT"; rm -f "$OUT/DONE"
job() { s=$1; tf=$2; o=$OUT/$s-$tf.csv.gz; [ -s "${o%.csv.gz}-agg.csv.gz" ] && return 0
  cd $W && /usr/bin/time -f "$s $tf %e s %M KB" -a -o $OUT/rss.log $PY sancho_l3_trades.py $SRC/$s.csv.gz $tf 2 $W/fund $OUT/$s-$tf.tmp.csv.gz >>$OUT/log.txt 2>>$OUT/err.log \
   && mv $OUT/$s-$tf.tmp.csv.gz $o && mv $OUT/$s-$tf.tmp-agg.csv.gz ${o%.csv.gz}-agg.csv.gz || echo "FAIL $s $tf" >>$OUT/err.log; }
export -f job; export OUT PY SRC W
for tf in 60 240 1440; do for f in $(ls -S $SRC/*USDT.csv.gz); do echo "$(basename $f .csv.gz) $tf"; done; done > $OUT/plan.txt
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $OUT/plan.txt
echo "jobs $(wc -l <$OUT/plan.txt) agg $(ls $OUT/*-agg.csv.gz | wc -l) fail $(grep -c FAIL $OUT/err.log)" > $OUT/DONE
