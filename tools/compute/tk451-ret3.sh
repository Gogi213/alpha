#!/bin/bash
# TK-451 возврат 3: агрегатор слоя 2, K=216, B=20000, события не пересчитываются. Метка: $OUT/DONE
set -u
W=/data/tk0451; OUT=$W/ret3; PY=/data/tk421/venv/bin/python
mkdir -p $OUT; rm -f $OUT/DONE; cd $W
$PY sancho_l2agg4.py $W/x3/l2-days.csv.gz $W/x3/l2-layer2-years.csv $OUT/l2 15 > $OUT/agg.log 2>&1
echo done > $OUT/DONE
