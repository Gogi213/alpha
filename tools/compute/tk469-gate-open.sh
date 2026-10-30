#!/bin/bash
# TK-469 гейт open2: новый вывод vs готовый tk461_open (open-bp3), сравнение после gunzip
P=/data/tk0469; EV=/data/tk0461/d2b-ev-bp3; OLD=/data/tk0461/open-bp3; PY=/data/tk421/venv/bin/python
export PYTHONPATH=/data/tk0455:/data/tk0461
mkdir -p $P/g; rm -f $P/g/*
for sym in LINKUSDT; do
  args=""; for f in $EV/$sym-*.csv.gz; do tf=$(basename $f .csv.gz); tf=${tf#*-}; args="$args $tf:$f:$P/g/$sym-$tf.csv.gz"; done
  s=$(date +%s.%N); taskset -c 15 $PY $P/tk469_open2.py /srv/alpha-sda/candles-1m/binance-um/$sym.csv.gz $args; e=$(date +%s.%N)
  echo "new wall $(echo "$e - $s" | bc) s"
  for f in $P/g/$sym-*.csv.gz; do b=$(basename $f); if cmp -s <(zcat $f) <(zcat $OLD/$b); then echo "IDENTICAL $b $(zcat $f | wc -l)"; else echo "DIFF $b"; fi; done
done
