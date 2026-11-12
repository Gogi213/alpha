#!/bin/bash
# TK-392: слой v2/v4 (цена исполнения, приближение) для монет без v3-пула. NX ядер (15).
set -e
cd /data/tk0380; O=tk392; NX=${NX:-15}; PY=/data/tk0380/npvenv/bin/python
mkdir -p $O/curve4
$PY tk392-mid-exec.py $O
tr -d '\015' < $O/pick4.csv | awk -F, 'NR>1{print $1, $4}' | while read c leg; do [ -s $O/mid4/$c.csv.gz ] && echo "$c $leg"; done > $O/list4.txt
xargs -P $NX -L1 sh -c '[ -s '$O'/curve4/$0-curve.csv ] || '$PY' tk392-event.py $0 '$O'/mid4/$0.csv.gz '$O'/curve4/$0 $1 > '$O'/curve4/$0.log 2>&1' < <(sed 's/ *$//' $O/list4.txt)
echo DONE > $O/run4.done
