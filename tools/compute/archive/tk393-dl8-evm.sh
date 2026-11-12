#!/usr/bin/env bash
# TK-393 докачка 8 (возврат Судьи 25.10, oom dl7): EVM-часть — Base v4, Polygon, Optimism с курсоров (avalanche DONE — пропуск). Заявка --mem 16.
# Курсор v4bcursor пишет сам tk393-sqd-v4b.py; здесь только если файла нет. Выход /data/tk0393/sqd7/.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7; T0=1792022400; T1=1792713599
mkdir -p "$R"; cd "$S" || exit 1
[ -f "$R/v4bcursor" ] || echo "52616000 52962126" > "$R/v4bcursor"
[ -f "$R/base-v4/DONE.json" ] || python3 -u tk393-sqd-v4b.py pools-old.csv "$R/v4bcursor" "$R/base-v4" >> "$R/base-v4.log" 2>&1 &
for c in polygon optimism; do
  [ -f "$R/$c/DONE.json" ] || python3 -u tk380-sqd-evm.py $c-mainnet pools-old.csv $c "$R" $T0 $T1 >> "$R/$c.log" 2>&1 &
done
wait
for c in polygon optimism avalanche; do
  [ -f "$R/$c/DONE.json" ] && python3 tk380-sqd-parse.py "$R/$c" "$R/$c-swaps" > "$R/parse-$c.log" 2>&1
done
echo '{"done": true}' > "$R/EVMDONE.json"
