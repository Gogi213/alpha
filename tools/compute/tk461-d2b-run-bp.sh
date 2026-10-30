#!/bin/bash
# TK-461: D2b bp (G1/G4/G41/G42) новым драйвером TK-467; события d2b-ev-bp2, ячейки d2b-cells-bp, выход d2b-out-bp
# bash tk467-d2b-run.sh [список пар "SYM tf" файлом]   выход по умолчанию /data/tk0461/d2b-out (OUT= — другой каталог), метка DONE
set -u
W=/data/tk0461; O=${OUT:-$W/d2b-out-bp}; PY=/data/tk421/venv/bin/python
export BIN=${BIN:-/data/tk0467/bin/tk467-pair-sim2} SRC=/srv/alpha-sda/candles-1m/binance-um EV=$W/d2b-ev-bp2 CELLS=$W/d2b-cells-bp FUND=/data/tk0455/fund TMP=$O/tmp O PY W
export PYTHONPATH=/data/tk0455:$W:/data/tk0467
mkdir -p $O $TMP; rm -f $O/DONE
if [ -n "${1:-}" ]; then cp "$1" $O/plan.txt; else ls $EV | grep -E '^[A-Z0-9]+-(60|240|1440)\.csv\.gz$' | sed 's/\.csv\.gz$//; s/-/ /' | sort -k2,2n -k1,1 > $O/plan.txt; fi
job() { o=$O/$1-$2.csv.gz; [ -s "$o" ] && return 0
  $PY /data/tk0467/tk467_d2b_run.py $1 $2 $o.tmp 2>>$O/err.log && mv $o.tmp $o || echo "FAIL $1 $2" >>$O/err.log; }
export -f job
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $O/plan.txt
echo "pairs $(wc -l <$O/plan.txt) done $(ls $O/*.csv.gz | wc -l) fail $(grep -c FAIL $O/err.log 2>/dev/null)" > $O/DONE
