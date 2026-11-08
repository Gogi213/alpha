#!/bin/bash
# TK-500: гейт «байт в байт» pq2bin (Rust, arrow-rs) против to_bin.py / to_bin_trades.py (pyarrow) на сутках DOGE 17.09; сервер 5.231.25.224.
# Эталонные .bin — /dev/shm/p40/{A,B,TA,TB}/<ЧЧ>.bin (сделаны Python). Запуск: bash /root/p40core/pq2bin_gate.sh
P=/root/p40core/pq2bin/target/release/pq2bin; T=/tmp/pq2bin_gate; rm -rf $T; mkdir -p $T
$P book /data/stakan/chft/binance_futures/2026-09-17 DOGEUSDT_orderbook.parquet $T/A 1
$P book /root/p40ref/raw/okx DOGE-USDT-SWAP_orderbook.parquet $T/B 1
$P trades /root/p40ref/raw/binance DOGEUSDT_trades.parquet $T/TA 1
$P trades /root/p40ref/raw/okx DOGE-USDT-SWAP_trades.parquet $T/TB 1
for d in A B TA TB; do bad=0; n=0; for f in /dev/shm/p40/$d/*.bin; do n=$((n+1)); cmp -s $f $T/$d/$(basename $f) || bad=$((bad+1)); done; echo "$d файлов=$n различий=$bad"; done
rm -rf $T
