#!/bin/bash
# TK-500: дым усечения §2.2 на DOGE 17.09 (5.231.25.224). Настоящие эпизоды L=500 почти все «одна нога»; позиции «обе исполнены» есть в K2. Берём первую позицию K2 «обе исполнены, выход мейкером» и режем годное время за 1 с до её выхода:
# дырой [EX-1000, EX+600000) -> ожидаем trunc=2, trunc_taker; концом данных EX-1000 -> trunc=1. Числа не интерпретируются.
cd /root/p40core/rs || exit 1
ARGS="/dev/shm/p40/A /dev/shm/p40/B /dev/shm/p40/TA /dev/shm/p40/TB 12300 12.3 500"
B=./target/release/p40core
P40_K2=1 nice -n 19 $B /tmp/t0.csv $ARGS >/dev/null 2>&1
R=$(awk -F, '$3=="both" && $4>0 {print $2","$4; exit}' /tmp/t0.csv.k2.exits.csv)
T0=${R%,*}; EX=${R#*,}
echo "K2 без флага: t0=$T0 exit=$EX"; grep ",$T0," /tmp/t0.csv.k2.exits.csv
printf "%s,%s\n" "$((EX-1000))" "$((EX+600000))" > /tmp/holes.txt
P40_K2=1 P40_TRUNC=1 P40_HOLES=/tmp/holes.txt nice -n 19 $B /tmp/t2.csv $ARGS >/dev/null 2>&1
echo "дыра:"; cut -d, -f3,5,8 /tmp/t2.csv.k2.exits.csv | sort | uniq -c; grep ",$T0," /tmp/t2.csv.k2.exits.csv
P40_K2=1 P40_TRUNC=1 P40_END_MS=$((EX-1000)) nice -n 19 $B /tmp/t1.csv $ARGS >/dev/null 2>&1
echo "конец данных:"; cut -d, -f3,5,8 /tmp/t1.csv.k2.exits.csv | sort | uniq -c; grep trunc_taker /tmp/t1.csv.k2.exits.csv | head -3
