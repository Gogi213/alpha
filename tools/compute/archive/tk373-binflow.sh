#!/usr/bin/env bash
# TK-373: поток Binance из aggTrades -> /data/tk0373/binflow/<SYM>/<день>.csv = 'сек,покупки_USD,продажи_USD' (агрессор: is_buyer_maker=false -> покупка). usage: tk373-binflow.sh <binance-dir> <out-dir> [P=15]
IN=$1; OUT=$2; P=${3:-15}; mkdir -p "$OUT"
one() { z=$1; b=$(basename "$z" .zip); sym=${b%%-aggTrades-*}; d=${b##*aggTrades-}; mkdir -p "$OUT/$sym"
  unzip -p "$z" | awk -F, 'NR>1{t=int($6/1000); u=$2*$3; if($7=="true")s[t]+=u; else by[t]+=u; seen[t]=1} END{for(t in seen) printf "%d,%.2f,%.2f\n",t,by[t],s[t]}' | sort -n -t, -k1,1 > "$OUT/$sym/$d.csv.tmp" && mv "$OUT/$sym/$d.csv.tmp" "$OUT/$sym/$d.csv"; }
export -f one; export OUT
find "$IN" -name '*.zip' | xargs -P "$P" -n1 bash -c 'one "$0"'
touch "$OUT/binflow.done"
