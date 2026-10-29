#!/usr/bin/env bash
# TK-453: докачка свежих дневных архивов Binance → verify → пересборка только затронутых монет → охват. Итог — /data/tk453/done.
set -eu
R=/srv/alpha-sda/candles-1m/binance-um
K="python3 /data/tk450/tk450-binance-klines.py"
$K map
$K dl
$K verify
S=""
for d in $R/raw/*/; do s=$(basename $d); [ -n "$(find $d -name '*.zip' -newer $R/$s.csv.gz -print -quit)" ] && S="$S $s"; done
echo "пересборка:$S"
[ -z "$S" ] || $K build $S
$K coverage
echo "ok $(date -u +%F\ %T) UTC; монет пересобрано: $(echo $S | wc -w)" > /data/tk453/done
