#!/bin/bash
# TK-451 слой 2: события + контроли C1/C2 по монетам × ТФ(1H,4H,1D) × k(2,1) (sancho_l2.py). Метка: $OUT/DONE; ошибки: $OUT/err.log
set -u
W=/data/tk0451; OUT=$W/l2; PY=/data/tk421/venv/bin/python
SRC=/srv/alpha-sda/candles-1m/binance-um
mkdir -p "$OUT"; rm -f "$OUT/DONE" "$OUT/err.log"
job() { s=$1; tf=$2; k=$3; f=$OUT/l2-$s-$tf-$k.csv.gz
  [ -s "$f" ] && return 0
  $PY $W/sancho_l2.py $SRC/$s.csv.gz $tf $k $f.tmp >/dev/null 2>>$OUT/err.log && mv $f.tmp $f || echo "FAIL $s $tf $k" >>$OUT/err.log; }
export -f job; export OUT PY SRC W
for tf in 60 240 1440; do for k in 2 1; do
  for f in $SRC/*USDT.csv.gz; do echo "$(basename $f .csv.gz) $tf $k"; done; done; done > $OUT/plan.txt
cat $OUT/plan.txt | xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1 $2'
echo "jobs $(wc -l <$OUT/plan.txt) fail $(grep -c FAIL $OUT/err.log 2>/dev/null)" > $OUT/DONE
