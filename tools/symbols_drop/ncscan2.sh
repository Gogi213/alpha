#!/bin/bash
cd /data/collect/bingx/data/2026-11-17
for f in book0?-1794842766.tsv.zst; do
  zstd -dc "$f" 2>/dev/null | awk -F'\t' 'BEGIN{while((getline s < "/data/tk584/nc-rem25.txt")>0) R[s]=1} {c+=$2} $1=="b" && ($5 in R) {h=int(c/1e9/900); n[h]++} END{for(h in n) printf "%d\t%d\n", h*900, n[h]}'
done | awk -F'\t' '{a[$1]+=$2} END{for(k in a) print k"\t"a[k]}' | sort -n > /data/tk584/nc-rem25-15min.tsv
