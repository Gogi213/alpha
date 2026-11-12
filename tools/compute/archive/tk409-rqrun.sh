#!/bin/bash
# TK-409 блок R с очередью (tk409-ev-rq.py): пул как у tk409-rrun.sh; ядер 15
B=/data/tk0393/tk409/Rq; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all PY S B
mkdir -p $B; cd $B; rm -f Rq.done; cp /data/tk0393/tk409/R/coins.lst coins.lst
one() { [ -f $B/$1.done ] && return; $PY $S/tk409-ev-rq.py $1 $2 $B/Rq-$1 > $B/Rq-$1.log 2>&1; touch $B/$1.done; }
export -f one
xargs -P 15 -L1 bash -c 'one $0 $1' < coins.lst
touch $B/Rq.done
