#!/bin/bash
# TK-392: пересчёт отклика (середина фьюча по сделкам) на готовых серединах /data/tk0380/tk392/mid
set -e
cd /data/tk0380; O=tk392; NX=${NX:-15}; PY=/data/tk0380/npvenv/bin/python
mkdir -p $O/curve2
ls $O/mid | sed 's/.csv.gz//' | xargs -P $NX -I{} sh -c '[ -s '$O'/curve2/{}-curve.csv ] || '$PY' tk392-event.py {} '$O'/mid/{}.csv.gz '$O'/curve2/{} > '$O'/curve2/{}.log 2>&1'
echo DONE > $O/run2.done
