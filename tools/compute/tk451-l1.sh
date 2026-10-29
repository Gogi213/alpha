#!/bin/bash
# TK-451 слой 1: таблицы событий по монетам × ТФ × k (sancho.py l1). Запуск: alsched submit … -- bash tk451-l1.sh
# Метка готовности: $OUT/DONE (пишется последней); ошибки по монетам — $OUT/err.log
set -u
W=/data/tk0451; OUT=$W/l1; PY=/data/tk421/venv/bin/python
SRC=/srv/alpha-sda/candles-1m/binance-um
mkdir -p "$OUT"; rm -f "$OUT/DONE" "$OUT/err.log"
job() { s=$1; tf=$2; k=$3; f=$OUT/l1-$s-$tf-$k.csv.gz
  [ -s "$f" ] && return 0
  $PY $W/sancho.py l1 $SRC/$s.csv.gz $tf $k $f.tmp >/dev/null 2>>$OUT/err.log && mv $f.tmp $f || echo "FAIL $s $tf $k" >>$OUT/err.log; }
export -f job; export OUT PY SRC W
# тяжёлые ТФ первыми
for tf in 5 15 30 60 240 1440 10080; do for k in 2 1; do
  for f in $SRC/*USDT.csv.gz; do echo "$(basename $f .csv.gz) $tf $k"; done; done; done > $OUT/plan.txt
for kk in 4 8; do for f in $SRC/*USDT.csv.gz; do echo "$(basename $f .csv.gz) 60 $kk"; done; done >> $OUT/plan.txt
cat $OUT/plan.txt | xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1 $2'
echo "jobs $(wc -l <$OUT/plan.txt) fail $(grep -c FAIL $OUT/err.log 2>/dev/null)" > $OUT/DONE
