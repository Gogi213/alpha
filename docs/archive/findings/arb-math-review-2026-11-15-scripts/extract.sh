#!/bin/sh
# raw top-of-book records of one symbol: rx_ms lag et bid bsz ask asz (rx from cumulative dns, as parse_inc)

cd /tmp/arbreview
x() { zstd -dc "$1" | awk -F'\t' -v S="$2" '{cum+=$2} $1=="b" && $5==S {printf "%.0f\t%s\t%s\t%s\t%s\t%s\t%s\n", cum/1e6, $3, $4, $6, $7, $8, $9}' > "$3"; }
x /data/collect/binance/data/2026-11-15/book05-1794692521.tsv.zst EVAAUSDT raw-binance-EVAA.tsv &
x /data/turbo-probe/soak-bybit/data/2026-11-15/book02-1794656680.tsv.zst EVAAUSDT raw-bybit-EVAA.tsv &
x /data/collect/bingx/data/2026-11-15/book01-1794656729.tsv.zst EVAA-USDT raw-bingx-EVAA.tsv &
wait
wc -l raw-*.tsv
