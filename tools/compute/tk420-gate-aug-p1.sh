#!/bin/bash
# TK-420 вар.3–5, B (Gate), август, шаг 1: месячные сделки Gate futures_usdt за 2026-08 (21 инстр.) -> свечи 1 м.
# Выход: /data/tk0393/tk420aug/candles/gate-fut/ИНСТР.csv.gz, сырьё удаляется; итог — p1.rc.
#   alsched submit --kind net --name tk420-gate-aug-p1 --max-runtime 2h --est 30m --cwd /data/tk0393/tk420aug -- bash /data/tk0393/tk420aug/tk420-gate-aug-p1.sh
set -u
cd /data/tk0393/tk420aug; rm -f p1.rc; mkdir -p gate-raw candles/gate-fut
awk -F'\t' 'NR==FNR{w[$3]=1;next} $1=="fut" && ($2 in w){print $1"\t"$2"\t"$3}' /data/tk420/tk420-gate-list.tsv /data/tk0393/tk411/gate-list.tsv > gate-aug-list.tsv
b() {
  c=$1; q=$2
  dst=candles/gate-fut/$c.csv.gz; [ -s "$dst" ] && return 0
  src=gate-raw/$c.csv.gz
  code=$(curl -s -m 1800 --retry 4 --retry-delay 5 -o "$src.part" -w '%{http_code}' "https://download.gatedata.org/futures_usdt/trades/202608/$c-202608.csv.gz")
  if [ "$code" = 200 ] && gzip -t "$src.part" 2>/dev/null; then mv "$src.part" "$src"; else rm -f "$src.part"; printf '%s\t%s\n' "$c" "$code" >> gate-aug-miss.tsv; return 0; fi
  python3 -I /data/tk0393/tk411/tk411-gate-candles.py fut "$src" "$q" "$dst.part" > "gate-raw/$c.cov" && mv "$dst.part" "$dst" && rm -f "$src" || echo "FAIL $c" >> gate-aug-miss.tsv
}
export -f b
rm -f gate-aug-miss.tsv
cut -f2,3 gate-aug-list.tsv | xargs -P4 -L1 bash -c 'b "$0" "$1"'
echo "n=$(ls candles/gate-fut | wc -l) miss=$(cat gate-aug-miss.tsv 2>/dev/null | wc -l)" > p1.log
echo 0 > p1.rc
