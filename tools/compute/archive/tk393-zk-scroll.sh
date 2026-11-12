#!/usr/bin/env bash
# TK-393 (v2: темп блоков замерен — v1 дал пустой диапазон, курсор за head): zkSync (13 пулов) + Scroll (3) через SQD Portal, 15–22.10 UTC; пулы из nochain-pools.csv. Выход /data/tk0393/sqd7/nc2.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7/nc2; T0=1792022400; T1=1792713599
mkdir -p "$R"; cd "$S" || exit 1
for c in zksync scroll; do
  [ -f "$R/$c/DONE.json" ] || python3 -u tk393-sqd-evm2.py $c-mainnet nochain-pools.csv $c "$R" $T0 $T1 >> "$R/$c.log" 2>&1 &
done
wait
for c in zksync scroll; do
  [ -f "$R/$c/DONE.json" ] && python3 tk380-sqd-parse.py "$R/$c" "$R/$c-swaps" > "$R/parse-$c.log" 2>&1
done
echo '{"done": true}' > "$R/ZKSCDONE.json"
