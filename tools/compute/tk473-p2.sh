#!/bin/bash
# TK-473 pass2: кандидаты -> pday (П) и H-файлы (H-файлы не читать до winners.csv). Метка /data/tk0473/p2/P2.DONE
set -u
O=/data/tk0473/p2; PY=/data/tk421/venv/bin/python; W=/data/tk0461; C=/data/tk0473/sel/cand.csv
mkdir -p $O; rm -f $O/P2.DONE; : >$O/err.log
job() { s=$1; f=$2; b=$(basename $f .csv.gz); mkdir -p $O/$s
  $PY $W/tk473_pass.py p2 $s $f $C $O/$s/$b >>$O/err.log 2>&1 || echo "FAIL $s $b" >>$O/err.log; }
export -f job; export W O PY C
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < /data/tk0472/agg/plan.txt
echo "ok $(grep -c FAIL $O/err.log)" >$O/P2.DONE
