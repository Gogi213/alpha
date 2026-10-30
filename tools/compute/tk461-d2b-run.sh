#!/bin/bash
# TK-461 D2b Санчо: ядро tk464-pair-sim по 252 парам (84 монеты x ТФ 60/240/1440), 15 процессов x 1 поток -> /data/tk0461/d2b-out/<SYM>-<tf>.csv.gz; метка d2b-out/DONE
# bash tk461-d2b-run.sh [список пар "SYM tf" файлом]
set -u
W=/data/tk0461; O=$W/d2b-out; PY=/data/tk421/venv/bin/python
export BIN=/data/tk0464/bin/tk464-pair-sim SRC=/srv/alpha-sda/candles-1m/binance-um EV=$W/d2b-ev CELLS=$W/d2b-cells FUND=/data/tk0455/fund TMP=$O/tmp O PY W
export PYTHONPATH=/data/tk0455:$W
mkdir -p $O $TMP; rm -f $O/DONE
if [ -n "${1:-}" ]; then cp "$1" $O/plan.txt; else ls $EV | grep -E '^[A-Z0-9]+-(60|240|1440)\.csv\.gz$' | sed 's/\.csv\.gz$//; s/-/ /' | sort -k2,2n -k1,1 > $O/plan.txt; fi
job() { o=$O/$1-$2.csv.gz; [ -s "$o" ] && return 0
  $PY $W/tk461_d2b_run.py $1 $2 $o.tmp 2>>$O/err.log && mv $o.tmp $o || echo "FAIL $1 $2" >>$O/err.log; }
export -f job
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $O/plan.txt
echo "pairs $(wc -l <$O/plan.txt) done $(ls $O/*.csv.gz | wc -l) fail $(grep -c FAIL $O/err.log 2>/dev/null)" > $O/DONE
