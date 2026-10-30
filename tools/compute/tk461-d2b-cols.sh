#!/bin/bash
# TK-461 D2b шаг 1: ss_bps/ot1_bps к событиям Санчо (§12.2 п.1) -> /data/tk0461/d2b-ev/<SYM>-<tf>.csv.gz; метка d2b-ev/DONE
set -u
W=/data/tk0461; OUT=$W/d2b-ev; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um
mkdir -p "$OUT"; rm -f "$OUT/DONE" "$OUT/probe.csv.gz"
export PYTHONPATH=/data/tk0455:$W OUT PY SRC W
job() { o=$OUT/$1-$2.csv.gz; [ -s "$o" ] && return 0; [ -s $W/ev/$1-$2.csv.gz ] || return 0
  $PY $W/tk461_d2b_cols.py $SRC/$1.csv.gz $2 $W/ev/$1-$2.csv.gz $o.tmp >>$OUT/log.txt 2>>$OUT/err.log && mv $o.tmp $o || echo "FAIL $1 $2" >>$OUT/err.log; }
export -f job
ls $W/ev/*.csv.gz | sed 's|.*/||; s|\.csv\.gz||; s|-| |' > $OUT/plan.txt
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $OUT/plan.txt
echo "jobs $(wc -l <$OUT/plan.txt) done $(ls $OUT/*.csv.gz | wc -l) fail $(grep -c FAIL $OUT/err.log 2>/dev/null)" > $OUT/DONE
