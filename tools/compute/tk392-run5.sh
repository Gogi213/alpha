#!/bin/bash
# TK-392: слой Solana (цена исполнения, приближение). NX ядер (15). После run4 (pick4.csv нужен для исключения).
set -e
cd /data/tk0380; O=tk392; NX=${NX:-15}; PY=/data/tk0380/npvenv/bin/python
mkdir -p $O/curve5
$PY tk392-mid-sol.py $O
xargs -P $NX -L1 sh -c '[ -s '$O'/curve5/$0-curve.csv ] || '$PY' tk392-event.py $0 '$O'/mid5/$0.csv.gz '$O'/curve5/$0 > '$O'/curve5/$0.log 2>&1' < $O/list5.txt
echo DONE > $O/run5.done
