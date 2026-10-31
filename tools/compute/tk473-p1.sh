#!/bin/bash
# TK-473 pass1: (key,sym,part) для О и П. Метка /data/tk0473/p1/P1.DONE
set -u
O=/data/tk0473/p1; PY=/data/tk421/venv/bin/python; W=/data/tk0461
mkdir -p $O; rm -f $O/P1.DONE; : >$O/err.log
job() { s=$1; f=$2; b=$(basename $f .csv.gz); mkdir -p $O/$s
  $PY $W/tk473_pass.py p1 $s $f $O/$s/$b.csv.gz >>$O/err.log 2>&1 || echo "FAIL $s $b" >>$O/err.log; }
export -f job; export W O PY
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < /data/tk0472/agg/plan.txt
echo "ok $(grep -c FAIL $O/err.log)" >$O/P1.DONE
