#!/bin/bash
# TK-526 (В-170): замер окна S_t на srv в отдельном дереве $D (бинарь гейта не трогаем): Fenwick против отсортированного Vec, 1,3 млн событий/сутки.
export PATH=$PATH:/root/.cargo/bin
D=${D:-/root/p40core-tk526b}
mkdir -p $D/rs && cp /root/p40core-tk526/rs/Cargo.toml /root/p40core-tk526/rs/Cargo.lock $D/rs/ && rm -rf $D/rs/src && cp -r /tmp/tk526src/src $D/rs/src
cd $D/rs || exit 1
nice -n 5 cargo test --release bench_window -- --ignored --nocapture 2>&1 | grep -E "Fenwick|sorted|test result|error" 
