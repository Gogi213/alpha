#!/bin/bash
# TK-522 (Судья 10.11 §7 п.7): сквозной смоук дыр на настоящих L1: DOGE Binance x Gate 28-30.09 (D1 кончается 30.09; дыры Gate: 29.09 02-03 ч и с 13 ч до конца — часовые снимки) (Gate 29.09 02-03 ч и 13 ч - 08.10 только часовые снимки = дыры, эпизоды до и между них), orch --validity-l1 -> эпизоды до/после дыры, trunc=2 у дыры.
# Запуск на srv: bash srv-gap-e2e.sh [out]; дерево tk522g, бинарь g87097f2c; числа исходов не читаются, проверяется только расположение эпизодов относительно дыр.
set -e
W=/root/p40core-tk522g; OUT=${1:-/tmp/gape2e}; rm -rf $OUT
cd $W
printf 'asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB,ctA,ctB\n1,5,binance,DOGEUSDT,,,,gate,DOGE_USDT,10,,,1,1\n' > $OUT.pairs.csv
DAYS=$(for i in $(seq 0 2); do date -u -d "2026-09-28 +$i day" +%F; done | paste -sd,)
P40_L1=1 P40_NOTRADE=through P40_CORE=/root/p40core/bin/p40core-nopgo-g87097f2c \
  python3 orch.py $OUT.pairs.csv $OUT --days $DAYS --chunk 3 --jobs 1 --l1-p 4 --box-conn 16 --l1-trades-raw --validity-l1 2>&1 | tail -3
D=$(ls -d $OUT/1/*)
echo "дыры: $(wc -l < $D/5.holes) интервалов"; cat $D/5.holes
python3 gap_e2e_check.py $D/5.exits.csv $D/5.holes --need-both
