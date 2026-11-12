#!/usr/bin/env bash
# TK-450: скачивание → проверка sha256 → сборка свечей Binance USDⓈ-M (после `tk450-binance-klines.py map`). usage: tk450-run.sh
set -eu
cd "$(dirname "$0")"
ROOT=/srv/alpha-sda/candles-1m/binance-um
python3 tk450-binance-klines.py dl
python3 tk450-binance-klines.py verify
bash progress.sh tk450 TK-450 сборка 0 1 шаг "verify → build"
python3 tk450-binance-klines.py build
bash progress.sh tk450 TK-450 готово 1 1 шаг ""
echo ok > "$ROOT/run.done"
