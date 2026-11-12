#!/bin/bash
B=/data/tk0393/tk409; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393
cd $B; rm -f Y.done
for V in 1000 2000 3000; do GEV=/data/tk0393/tk401/gev2all $PY $S/tk409-ev-yorsh.py Q /data/tk0380/tk392/mid/Q.csv.gz $B/YV$V $V > $B/YV$V.log 2>&1 & done
wait; touch $B/Y.done
