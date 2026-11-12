#!/usr/bin/env bash
# TK-450: докачка дневных архивов на дыры свечей Binance, пересборка затронутых монет, охват. usage: tk450-fill.sh
set -eu
cd "$(dirname "$0")"
ROOT=/srv/alpha-sda/candles-1m/binance-um
K="python3 tk450-binance-klines.py"
$K map
$K dl
$K fill
$K verify
AFF=$(cat "$ROOT/fill-affected.txt")
[ -z "$AFF" ] || $K build $AFF
$K coverage
bash progress.sh tk450 TK-450 готово 1 1 шаг ""
echo ok > "$ROOT/fill.done"
