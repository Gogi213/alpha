#!/bin/bash
# per (10-min bucket from file start, symbol, shard) book rows from prod files; row time = cumulative dns
out=/root/tk531s/symcount.tsv
: > $out
for f in /data/bnws/2026-11-11/book0?-1794432290.tsv.zst /data/bnws/2026-11-12/book0?-1794432290.tsv.zst; do
  sh=$(basename $f | cut -c1-6)
  zstdcat $f | awk -v sh=$sh -F'\t' '$1=="b"{t+=$2; b=int(t/6e11); c[b" "$5]++} END{for(k in c) print sh" "k" "c[k]}' >> $out
done
echo done > /root/tk531s/symcount.done
