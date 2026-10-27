#!/bin/bash
# TK-428 п.2: где в архиве download.gatedata.org лежит фандинг Gate (S3-листинг по marker, ключи после futures_usdt/f).
for p in futures_usdt/f futures_usdt/funding futures_usdt/g futures_usdt/o futures_usdt/m; do
  echo "== $p"
  curl -s -m 30 "https://download.gatedata.org/?marker=$p&max-keys=2" | grep -o '<Key>[^<]*</Key>'
done
for u in futures_usdt/funding_updates futures_usdt/funding_applies futures_usdt/funding_rates futures_usdt/funding; do
  for m in 202601 202607; do
    printf '%s %s ' "$u" "$m"; curl -s -o /dev/null -m 30 -w '%{http_code}\n' "https://download.gatedata.org/$u/$m/BTC_USDT-$m.csv.gz"
  done
done
