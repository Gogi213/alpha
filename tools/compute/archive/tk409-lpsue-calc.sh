#!/bin/bash
# TK-409 LAPTOP/SUE: хвост после докачки (середины, S(б)(в), Rq). Метка LS.done
B=/data/tk0393/tk409/LS; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all BASIS=ev
cd $B; rm -f LS.done
# середина: Sync -> ts,block,logidx,цена (SUE: r1/r0 = WBNB за SUE; LAPTOP: token0=USDC, цена = r0/r1)
sed 's#/data/tk0393/sqd10/#/data/tk0393/tk409/LS/sqd/#' $S/tk410/tk410-extract-all.py > ext.py
$PY ext.py pools.csv raw > ext.log 2>&1
$PY $S/tk409-lpsue-mid.py > mid.log 2>&1
for c in SUE LAPTOP; do
  ( $PY $S/tk409-ev-conv.py $c $B/mid-$c.csv.gz $B/Eb-$c 1000 b > $B/Eb-$c.log 2>&1; BASIS=ev $PY $S/tk409-ev-conv.py $c $B/mid-$c.csv.gz $B/Ec-$c 1000 c > $B/Ec-$c.log 2>&1
    $PY $S/tk409-ev-rq.py $c $B/mid-$c.csv.gz $B/Rq-$c > $B/Rq-$c.log 2>&1 ) &
done
wait; touch $B/LS.done
