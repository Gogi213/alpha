#!/bin/bash
# TK-522 (Судья 10.11 §2 п. 2): смоук возраста лучших цен на t0 -> assemble --stale-smax на настоящих L1: DOGE Binance x Gate 28-30.09, варианты k2 и k1p60, orch --age.
# Запуск на srv: bash srv-age-smoke.sh [out]; дерево tk522h (ядро с `age`), боевой p40-d1v4 не трогается (другое дерево, другой out).
set -e
W=/root/p40core-tk522h; OUT=${1:-/tmp/agesmoke}; rm -rf $OUT $OUT.pairs.csv
cd $W
printf 'asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB,ctA,ctB\n1,5,binance,DOGEUSDT,,,,gate,DOGE_USDT,10,,,1,1\n' > $OUT.pairs.csv
DAYS=$(for i in $(seq 0 2); do date -u -d "2026-09-28 +$i day" +%F; done | paste -sd,)
P40_L1=1 P40_NOTRADE=through P40_CORE=$W/rs/target/release/p40core \
  python3 orch.py $OUT.pairs.csv $OUT --days $DAYS --chunk 3 --jobs 1 --l1-p 4 --box-conn 16 --l1-trades-raw --validity-l1 --age \
  --variant k2:P40_K2=1 --variant k1p60:P40_SHIFT_B_MS=60000 2>&1 | tail -3
D=$(ls -d $OUT/1/*)
ls $D | grep age
for f in $D/5.age.csv $D/5.k1p60.age.csv; do echo "$f: $(($(wc -l < $f) - 1)) строк"; head -3 $f; done
python3 smax_l1.py $OUT/validity_l1.csv $OUT/smax_l1.csv | tail -3
for v in "" .k1p60; do
  python3 assemble.py $OUT/s$v.npz $OUT/s$v.json --day0 2026-09-28 --cens-s 600 --stale-smax $OUT/smax_l1.csv --pairs $OUT.pairs.csv 1:t0:5:0:$D/5$v
done
