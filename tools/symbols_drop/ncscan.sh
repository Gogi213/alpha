#!/bin/bash
cd /data/collect/bingx/data/2026-11-17
for f in book0?-1794842766.tsv.zst; do
  zstd -dc "$f" 2>/dev/null | awk -F'\t' '{c+=$2} $1=="b" && $5 ~ /^NC/ {n[$5]++; l[$5]=c; if(!($5 in fi)) fi[$5]=c} END{for(s in n) printf "%s\t%d\t%.0f\t%.0f\n", s, n[s], fi[s]/1e6, l[s]/1e6}'
done > /data/tk584/nc-last.tsv
echo done
