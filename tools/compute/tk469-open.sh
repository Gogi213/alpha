#!/bin/bash
# TK-469: турбо-вариант jobs tk461-open-bp2.sh — одна загрузка минут на монету для всех ТФ (tk469_open2.py); вывод тот же (после gunzip).
# EV=<d2b-ev dir с <SYM>-<tf>.csv.gz> OUT=<out dir> [NX=ядер]; метка $OUT/DONE. Гейт: /data/tk0469/gate_open.sh (IDENTICAL 5/5, LINKUSDT).
set -u
W=/data/tk0461; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um
: "${EV:?}" "${OUT:?}"
mkdir -p "$OUT"; rm -f "$OUT/DONE"
export PYTHONPATH=/data/tk0455:$W OUT PY SRC W EV
job() { sym=$1; args=""; for f in $EV/$sym-*.csv.gz; do tf=${f##*/}; tf=${tf%.csv.gz}; tf=${tf#*-}; o=$OUT/$sym-$tf.csv.gz; [ -s "$o" ] || args="$args $tf:$f:$o.tmp"; done
  [ -z "$args" ] && return 0
  $PY /data/tk0469/tk469_open2.py $SRC/$sym.csv.gz $args >>$OUT/log.txt 2>>$OUT/err.log || { echo "FAIL $sym" >>$OUT/err.log; return 0; }
  for a in $args; do o=${a##*:}; mv "$o" "${o%.tmp}"; done; }
export -f job
ls $EV/*.csv.gz | sed 's|.*/||; s|\.csv\.gz||; s|-[0-9]*$||' | sort -u > $OUT/plan.txt
xargs -P "${NX:-15}" -L1 bash -c 'job $0' < $OUT/plan.txt
echo "syms $(wc -l <$OUT/plan.txt) done $(ls $OUT/*.csv.gz | wc -l) fail $(grep -c FAIL $OUT/err.log 2>/dev/null)" > $OUT/DONE
