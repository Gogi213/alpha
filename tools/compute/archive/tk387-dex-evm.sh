#!/usr/bin/env bash
# TK-387 (G-016): свопы EVM-пулов кандидатов TK-386 через SQD Portal (без ключа), 01.09 00:00 — 14.10 23:59:59 UTC.
# Q и SUE — bsc, LAPTOP — base, GT — ethereum; для v2-пула SUE дополнительно Sync. Выход $R/<chain>/{part-*.jsonl,DONE.json}, $R/<chain>-swaps/swaps.csv.gz, ALLDONE.json.
# usage: tk387-dex-evm.sh <корень>   (на calc нужны /data/tk0387/{tk380-sqd-evm,tk380-sqd-parse,tk386-sqd-sync}.py и pools-tk387.csv)
export PYTHONUTF8=1 LC_ALL=C.UTF-8
R=${1:?корень}; S=/data/tk0387; T0=1788220800; T1=1792022399
mkdir -p "$R"; cd "$S" || exit 1
python3 -u tk380-sqd-evm.py binance-mainnet pools-tk387.csv bsc "$R" $T0 $T1 > "$R/bsc.log" 2>&1 &
python3 -u tk380-sqd-evm.py base-mainnet pools-tk387.csv base "$R" $T0 $T1 > "$R/base.log" 2>&1 &
python3 -u tk380-sqd-evm.py ethereum-mainnet pools-tk387.csv ethereum "$R" $T0 $T1 > "$R/eth.log" 2>&1 &
wait
grep -h "sqd: no response" "$R"/*.log
for c in bsc base ethereum; do
  [ -f "$R/$c/DONE.json" ] && python3 tk380-sqd-parse.py "$R/$c" "$R/$c-swaps" > "$R/parse-$c.log" 2>&1
done
# Sync (середина v2-пула SUE после свопа)
mkdir -p "$R/sync"
python3 -u tk386-sqd-sync.py binance-mainnet pools-tk387-v2.csv bsc "$R/sync" $T0 $T1 > "$R/sync.log" 2>&1
echo '{"done": true}' > "$R/ALLDONE.json"
