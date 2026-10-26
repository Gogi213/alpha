#!/bin/bash
# TK-409 пересчёт (б)/(в) с базой по событиям (возврат Судьи 26.10 07:19): BASIS=ev, N_b>=30 пар за 24 ч; пул 190 монет (список Sc); 15 ядер через alsched
B=/data/tk0393/tk409/E; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all BASIS=ev PY S B
mkdir -p $B; cd $B; rm -f E.done
cp /data/tk0393/tk409/Sc/coins.lst coins.lst
one() { [ -f $B/$1.done ] && return; $PY $S/tk409-ev-conv.py $1 $2 $B/Eb-$1 1000 b > $B/Eb-$1.log 2>&1; $PY $S/tk409-ev-conv.py $1 $2 $B/Ec-$1 1000 c > $B/Ec-$1.log 2>&1; touch $B/$1.done; }
export -f one
xargs -P 15 -L1 bash -c 'one $0 $1' < coins.lst
touch $B/E.done
