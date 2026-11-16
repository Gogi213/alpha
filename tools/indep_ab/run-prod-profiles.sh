#!/bin/bash
H=$1; OUT=$2; mkdir -p $OUT
export FINE=1
for spec in "okx /data/collect/okx/data" "bybit /data/turbo-probe/soak-bybit/data" "gate /data/collect/gate/data" "bingx /data/collect/bingx/data" "binance /data/collect/binance/data"; do
  set -- $spec
  for kind in trade book; do
    python3 /data/indep-ab/prod_hour_profile.py $2 $kind $H > $OUT/$1-$kind.txt 2>/dev/null
  done
done
