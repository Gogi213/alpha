#!/usr/bin/env bash
# TK-387 (G-016): докачка EVM-свопов SQD параллельными отрезками (последовательно ≈7 с/запрос: base ≈7 ч, bsc ≈4 ч).
# Берёт курсоры прежнего tk387-dex-evm.sh (корень $1; прежнее задание остановлено), режет остаток [b,end] на K отрезков,
# каждый — свой процесс tk380-sqd-evm.py; части сливаются жёсткими ссылками в $R/<цепь>/part-*.jsonl (порядок: голова, отрезки),
# дальше parse как раньше. Sync (SUE v2, bsc) — те же отрезки по блокам t0..t1 из bsc.log. Повторный запуск идемпотентен (курсоры отрезков).
export PYTHONUTF8=1 LC_ALL=C.UTF-8
R=${1:?корень}; S=/data/tk0387; T0=1788220800; T1=1792022399
KB=${KB:-10}; KS=${KS:-8}; KE=${KE:-3}; KY=${KY:-6}
cd "$S" || exit 1
mkdir -p "$R/seg"
# start <скрипт> <датасет> <csv> <цепь> <отрезков> <метка> <lo> <hi>: отрезки [lo,hi] -> $R/seg/<метка>-k
start() {
  local py=$1 ds=$2 csv=$3 c=$4 K=$5 tag=$6 lo=$7 hi=$8 k a z d
  for ((k = 0; k < K; k++)); do
    a=$((lo + (hi - lo + 1) * k / K)); z=$((lo + (hi - lo + 1) * (k + 1) / K - 1))
    d="$R/seg/$tag-$k"; mkdir -p "$d/$c"
    [ -f "$d/$c/cursor" ] || printf '%s %s' "$a" "$z" > "$d/$c/cursor"
    python3 -u "$py" "$ds" "$csv" "$c" "$d" $T0 $T1 > "$d/run.log" 2>&1 &
  done
}
# merge <метка> <отрезков> <куда> <голова|-> : ссылки частей по порядку, DONE.json
merge() {
  local tag=$1 K=$2 dst=$3 head=$4 c=$5 n=0 d f k
  for ((k = 0; k < K; k++)); do [ -f "$R/seg/$tag-$k/$c/DONE.json" ] || { echo "отрезок $tag-$k не готов"; return 1; }; done
  mkdir -p "$dst"; rm -f "$dst"/part-*.jsonl
  for d in ${head:+"$head"} $(for ((k = 0; k < K; k++)); do echo "$R/seg/$tag-$k/$c"; done); do
    for f in $(ls "$d"/part-*.jsonl 2>/dev/null | sort); do ln "$f" "$dst/part-$(printf %05d $n).jsonl"; n=$((n + 1)); done
  done
  echo "{\"done\": true, \"parts\": $n}" > "$dst/DONE.json"
}
# головы: прежние каталоги цепей (части + курсор) переезжают в <цепь>-head
for c in bsc base ethereum; do [ -d "$R/$c-head" ] || { [ -d "$R/$c" ] && mv "$R/$c" "$R/$c-head"; }; done
read -r bb be < "$R/bsc-head/cursor"; read -r sb se < <(sed -n 1p "$R/bsc.log" | awk '{print $5, $6}')
start tk380-sqd-evm.py binance-mainnet pools-tk387.csv bsc "$KS" bsc "$bb" "$be"
read -r xb xe < "$R/base-head/cursor"; start tk380-sqd-evm.py base-mainnet pools-tk387.csv base "$KB" base "$xb" "$xe"
read -r eb ee < "$R/ethereum-head/cursor"; start tk380-sqd-evm.py ethereum-mainnet pools-tk387.csv ethereum "$KE" ethereum "$eb" "$ee"
start tk386-sqd-sync.py binance-mainnet pools-tk387-v2.csv bsc "$KY" sync "$sb" "$se"
wait
grep -h "sqd: no response" "$R"/seg/*/run.log
rc=0
merge bsc "$KS" "$R/bsc" "$R/bsc-head" bsc || rc=1
merge base "$KB" "$R/base" "$R/base-head" base || rc=1
merge ethereum "$KE" "$R/ethereum" "$R/ethereum-head" ethereum || rc=1
merge sync "$KY" "$R/sync/bsc" "" bsc || rc=1
for c in bsc base ethereum; do [ -f "$R/$c/DONE.json" ] && python3 tk380-sqd-parse.py "$R/$c" "$R/$c-swaps" > "$R/parse-$c.log" 2>&1; done
[ $rc = 0 ] && echo '{"done": true}' > "$R/ALLDONE.json"
exit $rc
