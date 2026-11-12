#!/usr/bin/env bash
# TK-393 (возврат Судьи 26.10, п.4): 4 BSC-монеты Gate с китайскими тикерами (26 пулов, cn5-pools.csv) 15–22.10 UTC через SQD Portal; выход /data/tk0393/sqd7/cn5.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7/cn5; mkdir -p "$R"; cd "$S" || exit 1
python3 -u tk393-sqd-evm2.py binance-mainnet cn5-pools.csv bsc "$R" 1792022400 1792713599 >> "$R/dl.log" 2>&1 || exit 1
python3 tk380-sqd-parse.py "$R/bsc" "$R/swaps" > "$R/parse.log" 2>&1
echo '{"done": true}' > "$R/CN5DONE.json"
