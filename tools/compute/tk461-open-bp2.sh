#!/bin/bash
# TK-461 D2b: «открытие» k/M/v для остальных G bp (g2,g3,g32,g5,g6,g61,g63,g7,g8): stage2 -> d2b-ev-bp3, tk461_open.py -> open-bp3; метка open-bp3/DONE
set -u
W=/data/tk0461; EV=$W/d2b-ev-bp3; OUT=$W/open-bp3; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um; B=$W/bpev/out
mkdir -p "$OUT"; rm -f "$OUT/DONE"
export PYTHONPATH=/data/tk0455:$W OUT PY SRC W EV
$PY -I $W/tk461_bp_stage2.py $EV $B/bp-g2.csv.gz $B/bp-g3.csv.gz $B/bp-g32.csv.gz $B/bp-g5.csv.gz $B/bp-g6.csv.gz $B/bp-g61.csv.gz $B/bp-g63.csv.gz $B/bp-g7.csv.gz $B/bp-g8.csv.gz >>$OUT/log.txt 2>>$OUT/err.log || { echo "FAIL stage" >>$OUT/err.log; exit 1; }
job() { o=$OUT/$1-$2.csv.gz; [ -s "$o" ] && return 0; [ -s $EV/$1-$2.csv.gz ] || return 0
  $PY $W/tk461_open.py $SRC/$1.csv.gz $2 $EV/$1-$2.csv.gz /data/tk0455/fund $o.tmp >>$OUT/log.txt 2>>$OUT/err.log && mv $o.tmp $o || echo "FAIL $1 $2" >>$OUT/err.log; }
export -f job
ls $EV/*.csv.gz | sed 's|.*/||; s|\.csv\.gz||; s|-| |' > $OUT/plan.txt
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $OUT/plan.txt
echo "jobs $(wc -l <$OUT/plan.txt) done $(ls $OUT/*.csv.gz | wc -l) fail $(grep -c FAIL $OUT/err.log 2>/dev/null)" > $OUT/DONE
