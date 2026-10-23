#!/bin/bash
# TK-379: размер часовых файлов стакана Gate по одному контракту за 15–22.10 (HEAD, шаг 3 ч).
c=${1:-BTC_USDT}
for d in 15 16 17 18 19 20 21 22; do
  for h in 00 03 06 09 12 15 18 21; do
    printf '%s%s ' "$d" "$h"
    curl -sI -m 20 "https://download.gatedata.org/futures_usdt/orderbooks/202610/$c-202610$d$h.csv.gz" | tr -d '\r' |
      awk 'NR==1{c=$2} tolower($1)=="content-length:"{n=$2} END{print c, n}'
  done
done | paste - - - -
