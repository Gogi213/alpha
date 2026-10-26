#!/bin/bash
# TK-409 проба Q v2 (возврат Судьи 26.10 04:40): S с видимостью свопа tv = ts+V (V=1000 главная, 2000/3000 чувствительность)
B=/data/tk0393/tk409; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393
cd $B; rm -f Qv.done
for V in 1000 2000 3000; do GEV=/data/tk0393/tk401/gev2all $PY $S/tk409-ev-conv.py Q /data/tk0380/tk392/mid/Q.csv.gz $B/QV$V $V > $B/QV$V.log 2>&1 & done
wait; touch $B/Qv.done
