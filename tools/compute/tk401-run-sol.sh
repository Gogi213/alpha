#!/bin/bash
# TK-401: слой Solana новых mint (цена исполнения). 15 ядер; середина WSOL-квоты × сделки Gate SOL_USDT (нога SOL).
set -e
O=/data/tk0393/tk401; NX=${NX:-15}; PY=/data/tk0380/npvenv/bin/python; cd /data/tk0380
mkdir -p $O/curve-sol
$PY /data/tk0393/tk401-mid-sol.py $O
xargs -r -P $NX -L1 sh -c '[ -s '$O'/curve-sol/$0-curve.csv ] || '$PY' tk392-event.py $0 '$O'/mid-sol/$0.csv.gz '$O'/curve-sol/$0 $1 > '$O'/curve-sol/$0.log 2>&1' < <(sed 's/ *$//' $O/list-sol.txt)
echo DONE > $O/runsol401.done
