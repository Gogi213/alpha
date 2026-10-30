#!/bin/bash
# TK-461 D2b: выборки «открытия» k/M/v для bp (G1/G4/G41/G42): split по (sym,tf) -> d2b-ev-bp, затем tk461_open.py -> open-bp; метка open-bp/DONE
set -u
W=/data/tk0461; EV=$W/d2b-ev-bp; OUT=$W/open-bp; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um
mkdir -p "$OUT"; rm -f "$OUT/DONE"
export PYTHONPATH=/data/tk0455:$W OUT PY SRC W EV
$PY -I $W/tk461_bp_split.py $EV $W/bpev/out/bp-g1.csv.gz $W/bpev/out/bp-g4.csv.gz $W/bpev/out/bp-g41.csv.gz $W/bpev/out/bp-g42.csv.gz >>$OUT/log.txt 2>>$OUT/err.log || { echo "FAIL split" >>$OUT/err.log; exit 1; }
job() { o=$OUT/$1-$2.csv.gz; [ -s "$o" ] && return 0; [ -s $EV/$1-$2.csv.gz ] || return 0
  $PY $W/tk461_open.py $SRC/$1.csv.gz $2 $EV/$1-$2.csv.gz /data/tk0455/fund $o.tmp >>$OUT/log.txt 2>>$OUT/err.log && mv $o.tmp $o || echo "FAIL $1 $2" >>$OUT/err.log; }
export -f job
ls $EV/*.csv.gz | sed 's|.*/||; s|\.csv\.gz||; s|-| |' > $OUT/plan.txt
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $OUT/plan.txt
echo "jobs $(wc -l <$OUT/plan.txt) done $(ls $OUT/*.csv.gz | wc -l) fail $(grep -c FAIL $OUT/err.log 2>/dev/null)" > $OUT/DONE
