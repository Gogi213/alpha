#!/bin/bash
# TK-451 возврат Судьи: l1 по годам/ранним-поздним (k=1,2) + агрегатор слоя 2 с A2/A3. Метка: $OUT/DONE
set -u
W=/data/tk0451; OUT=$W/ret1; PY=/data/tk421/venv/bin/python
mkdir -p $OUT; rm -f $OUT/DONE; cd $W
for k in 1 2; do $PY sancho_l1years.py $W/l1 $OUT/l1years-k$k.csv $k > $OUT/l1years-k$k.log 2>&1 & done
NX=11 $PY sancho_l2agg.py $W/l2 $OUT/l2 11 > $OUT/l2agg.log 2>&1
wait
echo done > $OUT/DONE
