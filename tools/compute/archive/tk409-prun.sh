#!/bin/bash
# TK-409 дельта пула (возврат Судьи 26.10 06:00): класс P — Q (из пробы V1000) + практики Solana с серединой tk401/mid-sol (CLAUDIA, FONE, KIMCHI, PAID); S и Y, V=1000
B=/data/tk0393/tk409/pool; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all
cd $B; rm -f pdelta.done
for x in cells counts eps manual.txt; do :; done
for f in QV1000-cells.csv QV1000-counts.csv QV1000-eps.csv QV1000-manual.txt QV1000.log; do cp /data/tk0393/tk409/$f $B/S-Q${f#QV1000}; done
for f in YV1000-cells.csv YV1000-counts.csv YV1000-eps.csv YV1000-manual.txt YV1000.log; do cp /data/tk0393/tk409/$f $B/Y-Q${f#YV1000}; done
for c in CLAUDIA FONE KIMCHI PAID; do m=/data/tk0393/tk401/mid-sol/$c.csv.gz
  ( $PY $S/tk409-ev-conv.py $c $m $B/S-$c 1000 > $B/S-$c.log 2>&1; $PY $S/tk409-ev-yorsh.py $c $m $B/Y-$c 1000 > $B/Y-$c.log 2>&1 ) &
done
wait; touch $B/pdelta.done
