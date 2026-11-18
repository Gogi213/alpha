#!/usr/bin/env bash
# П-42 §13.23.1 (TK-598): вход окна 14–16.11 после докачки /data/p42-holdout (18.11 09:53; 0GUSDT, 龙虾USDT — все дни).
# Дерево по образцу tk592_hold_prep.sh, но свой каталог и только 14–16.11 (17.11 в протоколе нет); исходные каталоги не меняются.
#   klines/<SYM>/<день>.csv + funding.csv, aggtrades/<SYM>/<SYM>-aggTrades-<день>.zip, затем `breakout convert` → cache (P42_T0=2026-10-31).
# Запуск на srv под systemd-run (Nice=19): bash tk598_hold_prep.sh   (идемпотентно).
set -euo pipefail
W=/data/p42-tk598/hold-in
H=/data/p42-holdout
BIN=/data/p42-t566/bin/breakout-v21
DAYS="2026-11-14 2026-11-15 2026-11-16"
mkdir -p "$W/klines" "$W/aggtrades"
tr ',' '\n' < /data/p42-t566/symbols.txt | grep -v '^$' > "$W/symbols.list"
n=0
while read -r s; do
  mkdir -p "$W/klines/$s" "$W/aggtrades/$s"
  ln -sf /data/klines/binance/"$s"/20??-??-??.csv "$W/klines/$s/" 2>/dev/null || true
  ln -sf "/data/klines/binance/$s/funding.csv" "$W/klines/$s/funding.csv"
  ln -sf /data/aggtrades/binance/"$s"/*.zip "$W/aggtrades/$s/" 2>/dev/null || true
  for d in $DAYS; do
    a="$H/aggtrades/$s/$s-aggTrades-$d.zip"
    [ -e "$a" ] && ln -sf "$a" "$W/aggtrades/$s/"
    z="$H/klines/$s/$s-1m-$d.zip"
    [ -e "$z" ] && { unzip -p "$z" > "$W/klines/$s/$d.csv.tmp" && mv "$W/klines/$s/$d.csv.tmp" "$W/klines/$s/$d.csv"; }
  done
  n=$((n+1))
done < "$W/symbols.list"
echo "монет $n; суток минуток BTCUSDT $(ls "$W"/klines/BTCUSDT/20??-??-??.csv | wc -l), zip сделок BTCUSDT $(ls "$W"/aggtrades/BTCUSDT/*.zip | wc -l)"
# манифест полноты 14–16.11: монет × дней по минуткам и по сделкам
for d in $DAYS; do
  k=$(ls "$W"/klines/*/"$d".csv 2>/dev/null | wc -l); a=$(ls "$W"/aggtrades/*/*-aggTrades-"$d".zip 2>/dev/null | wc -l)
  echo "$d klines=$k aggtrades=$a"
done > "$W/coverage.txt"
cat "$W/coverage.txt"
rm -rf "$W/cache"
P42_T0=2026-10-31 "$BIN" breakout convert --src "$W/klines" --out "$W/cache" --threads 2 > "$W/convert.log" 2>&1
echo $? > "$W/convert.rc"
