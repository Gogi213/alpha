#!/usr/bin/env bash
# TK-487: convert_universe.sh (все биржи) → сверка трёх символов с сырыми свечами → $W/verify.txt. rc пишет alsched.
#   convert_universe_run.sh <бинарь alpha> <потоки>
set -euo pipefail
BIN="${1:?бинарь}"; TH="${2:?потоки}"
W="${W:-/data/tk0487}"; C="${C:-/srv/alpha-sda/candles-1m}"; S="${S:-$(cd "$(dirname "$0")" && pwd)}"
bash "$S/convert_universe.sh" "$BIN" "$TH"
python3 "$S/verify_panels.py" \
  "BTCUSDT@binance-um=$W/panels-binance-um/BTCUSDT.sap,$C/binance-um/BTCUSDT.csv.gz" \
  "ETHUSDT@bybit-linear=$W/panels-bybit-linear/ETHUSDT.sap,$C/bybit-linear/ETHUSDT.csv.gz" \
  "SOL_USDT@gate-futures_usdt=$W/panels-gate-futures_usdt/SOL_USDT.sap,$C/gate-futures_usdt/SOL_USDT.csv.gz" | tee "$W/verify.txt"
