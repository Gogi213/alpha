#!/bin/bash
# TK-500: тесты и релизная сборка ядра на 5.231.25.224 (/root/p40core/rs; P40_DIR/P40_NEW — другое дерево и выход, TK-515), затем гейт «байт в байт» на DOGE 17.09 против эталонных таблиц $1 (префикс, без флагов среды).
export PATH=$PATH:/root/.cargo/bin
P=${P40_DIR:-/root/p40core}
NEW=${P40_NEW:-/tmp/new.csv}
cd $P/rs || exit 1
cargo test --release 2>&1 | tail -${TAIL:-12}
cargo build --release 2>&1 | tail -2
ARGS="/dev/shm/p40/A /dev/shm/p40/B /dev/shm/p40/TA /dev/shm/p40/TB 12300 12.3 500"
nice -n 19 ./target/release/p40core $NEW $ARGS 2>&1 | tail -1
for f in "" .fills.csv .exits.csv .features.csv .path.csv; do cmp /tmp/${1:-old}.csv$f $NEW$f && echo "байт в байт: $f"; done
