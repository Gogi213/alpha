#!/bin/bash
# TK-522: тесты Rust ядра в дереве $1 (умолч. /root/p40core-tk522g), фильтр $2 — на 5.231.25.224, вывод хвостом
export PATH=$PATH:/root/.cargo/bin
D=${1:-/root/p40core-tk522g}
cd $D/rs || exit 1
touch src/*.rs
nice -n 19 cargo test --release ${2:-} 2>&1 | tail -${TAIL:-12}
