#!/bin/bash
# TK-461 шаг D1: события Санчо 84 монеты × ТФ 60/240/1440 -> /data/tk0461/ev/<SYM>-<tf>.csv.gz; метка /data/tk0461/ev/DONE
set -u
W=/data/tk0461; OUT=$W/ev; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um
mkdir -p "$OUT"; rm -f "$OUT/DONE"
export PYTHONPATH=/data/tk0455 OUT PY SRC W
job() { o=$OUT/$1-$2.csv.gz; [ -s "$o" ] && return 0
  $PY $W/tk461_events.py $SRC/$1.csv.gz $2 2 $o.tmp >>$OUT/log.txt 2>>$OUT/err.log && mv $o.tmp $o || echo "FAIL $1 $2" >>$OUT/err.log; }
export -f job
for tf in 60 240 1440; do for f in $(ls -S $SRC/*USDT.csv.gz); do echo "$(basename $f .csv.gz) $tf"; done; done > $OUT/plan.txt
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $OUT/plan.txt
echo "jobs $(wc -l <$OUT/plan.txt) done $(ls $OUT/*.csv.gz | wc -l) fail $(grep -c FAIL $OUT/err.log 2>/dev/null)" > $OUT/DONE
