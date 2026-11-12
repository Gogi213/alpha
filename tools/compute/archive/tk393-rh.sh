#!/usr/bin/env bash
# TK-393: Robinhood chain (30 пулов) через RPC getLogs; пулы из nochain-pools.csv
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7; cd "$S" || exit 1
python3 -u tk393-rpc-logs.py https://rpc.mainnet.chain.robinhood.com nochain-pools.csv robinhood "$R/nc" >> "$R/nc-robinhood.log" 2>&1
echo done > "$R/nc/ROBINHOOD.done"
