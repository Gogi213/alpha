#!/bin/bash
# TK-409 S форма (в) «мейкер на стороне схождения, очередь по сделкам» на пуле (возврат Судьи 26.10 06:33); V=1000; 15 ядер
B=/data/tk0393/tk409/Sc; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all PY S B
mkdir -p $B; cd $B; rm -f Sc.done
cp /data/tk0393/tk409/Sb/coins.lst coins.lst; for c in CLAUDIA FONE PAID; do grep -q "^$c " coins.lst || echo "$c /data/tk0393/tk401/mid-sol/$c.csv.gz sol" >> coins.lst; done
one() { [ -f $B/$1.done ] && return; $PY $S/tk409-ev-conv.py $1 $2 $B/Sc-$1 1000 c > $B/Sc-$1.log 2>&1; touch $B/$1.done; }
export -f one
xargs -P 15 -L1 bash -c 'one $0 $1' < coins.lst
touch $B/Sc.done
