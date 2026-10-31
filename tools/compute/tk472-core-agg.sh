#!/bin/bash
# TK-472: сводка всех d2b-out-* для кора монет -> /data/tk0472/agg/{src}/*.csv.gz; метка /data/tk0472/agg/DONE
set -u
W=/data/tk0461; O=/data/tk0472/agg; PY=/data/tk421/venv/bin/python
mkdir -p $O; rm -f $O/DONE
job() { s=$1; f=$2; b=$(basename $f .csv.gz); mkdir -p $O/$s; [ -s $O/$s/$b.day.csv.gz ] && return 0
  $PY $W/tk472_core_agg.py $s $f $O/$s/$b >>$O/err.log 2>&1 || echo "FAIL $s $b" >>$O/err.log; }
export -f job; export W O PY
for p in sancho:d2b-out bp:d2b-out-bp bp3:d2b-out-bp3 s:d2b-out-s; do s=${p%%:*}; d=${p##*:}; ls $W/$d/*.csv.gz | sed "s|^|$s |"; done > $O/plan.txt
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $O/plan.txt
echo "jobs $(wc -l <$O/plan.txt) done $(ls $O/*/*.day.csv.gz | wc -l) fail $(grep -c FAIL $O/err.log 2>/dev/null)" > $O/DONE
