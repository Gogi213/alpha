#!/usr/bin/env bash
# П-42 §13.18 (TK-592): деревья для движка v20 с отложенными сутками — минутки (`<SYM>/<день>.csv` + funding.csv) и сделки (`<SYM>/<SYM>-aggTrades-<день>.zip`),
# исходные каталоги не меняются: прежние сутки — символьные ссылки, новые — из /data/p42-holdout (zip минуток распаковываются в CSV).
# Запуск на srv: bash tk592_hold_prep.sh   (идемпотентно; повторить после докачки новых суток `dl_holdout.sh`)
set -euo pipefail
W=/data/tk592
H=/data/p42-holdout
mkdir -p "$W/klines" "$W/aggtrades"
tr ',' '\n' < /data/p42-t566/symbols.txt | grep -v '^$' > "$W/symbols.list"
n=0
while read -r s; do
  mkdir -p "$W/klines/$s" "$W/aggtrades/$s"
  ln -sf /data/klines/binance/"$s"/20??-??-??.csv "$W/klines/$s/" 2>/dev/null || true
  ln -sf "/data/klines/binance/$s/funding.csv" "$W/klines/$s/funding.csv"
  ln -sf /data/aggtrades/binance/"$s"/*.zip "$W/aggtrades/$s/" 2>/dev/null || true
  if [ -d "$H/aggtrades/$s" ]; then ln -sf "$H"/aggtrades/"$s"/*.zip "$W/aggtrades/$s/" 2>/dev/null || true; fi
  for z in "$H"/klines/"$s"/*-1m-*.zip; do
    [ -e "$z" ] || continue
    d=$(basename "$z" .zip); d=${d##*-1m-}
    unzip -p "$z" > "$W/klines/$s/$d.csv.tmp" && mv "$W/klines/$s/$d.csv.tmp" "$W/klines/$s/$d.csv"
  done
  n=$((n+1))
done < "$W/symbols.list"
echo "монет $n; суток минуток $(ls "$W"/klines/BTCUSDT/20??-??-??.csv | wc -l), zip сделок BTCUSDT $(ls "$W"/aggtrades/BTCUSDT/*.zip | wc -l)"
