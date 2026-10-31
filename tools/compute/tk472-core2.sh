#!/bin/bash
# TK-472 v2: stage1 (годные конфиги) -> сутки по G -> stage3. Метка /data/tk0472/v2/CORE2.DONE
set -u
W=/data/tk0461; A=/data/tk0472/agg; O=/data/tk0472/v2; PY=/data/tk421/venv/bin/python
mkdir -p $O; rm -f $O/CORE2.DONE; : >$O/err.log
$PY $W/tk472_core2.py stage1 $A $O >>$O/err.log 2>&1 || { echo FAIL1 >$O/CORE2.DONE; exit 1; }
job() { s=$1; f=$2; b=$(basename $f .csv.gz); mkdir -p $O/dayg/$s
  $PY $W/tk472_dayg.py $s $f $O/good.csv $O/dayg/$s/$b.csv.gz >>$O/err.log 2>&1 || echo "FAIL $s $b" >>$O/err.log; }
export -f job; export W O PY
xargs -P "${NX:-8}" -L1 bash -c 'job $0 $1' < $A/plan.txt
$PY $W/tk472_core2.py stage3 $A $O/dayg ${CANDLES:-/data/tk0461/candles} $O >$O/core2.log 2>>$O/err.log || { echo FAIL3 >$O/CORE2.DONE; exit 1; }
echo ok >$O/CORE2.DONE
