#!/bin/bash
# TK-409 S форма (б) «разворот» на пуле (возврат Судьи 26.10 06:05): те же монеты, что блок R + Q; V=1000; ядер 15
B=/data/tk0393/tk409/Sb; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all PY S B
mkdir -p $B; cd $B; rm -f Sb.done
cp /data/tk0393/tk409/R/coins.lst coins.lst; grep -q '^Q ' coins.lst || echo "Q /data/tk0380/tk392/mid/Q.csv.gz evm" >> coins.lst
one() { [ -f $B/$1.done ] && return; $PY $S/tk409-ev-conv.py $1 $2 $B/Sb-$1 1000 b > $B/Sb-$1.log 2>&1; touch $B/$1.done; }
export -f one
xargs -P 15 -L1 bash -c 'one $0 $1' < coins.lst
touch $B/Sb.done
