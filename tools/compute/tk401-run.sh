#!/bin/bash
# TK-401: отклик Gate на ход DEX (метод TK-392) на НОВЫХ пулах TK-393 (EVM). 15 ядер. Середина Gate: сделки (event.py); фьюч-BBO TK-394 — где есть.
set -e
O=/data/tk0393/tk401; NX=${NX:-15}; PY=/data/tk0380/npvenv/bin/python; cd /data/tk0380
mkdir -p $O/curve $O/cb
$PY /data/tk0393/tk401-mid.py $O
xargs -r -P $NX -L1 sh -c '[ -s '$O'/curve/$0-curve.csv ] || '$PY' tk392-event.py $0 '$O'/mid/$0.csv.gz '$O'/curve/$0 $1 > '$O'/curve/$0.log 2>&1' < <(sed 's/ *$//' $O/list.txt)
while read c leg; do [ -s /data/tk0394/bbo/$c.csv.gz ] && echo "$c $leg" || true; done < <(sed 's/ *$//' $O/list.txt) > $O/list-bbo.txt
xargs -r -P $NX -L1 sh -c '[ -s '$O'/cb/$0-curve.csv ] || '$PY' tk398-event-bbo.py $0 '$O'/mid/$0.csv.gz '$O'/cb/$0 $1 > '$O'/cb/$0.log 2>&1' < <(sed 's/ *$//' $O/list-bbo.txt)
echo DONE > $O/run401.done
