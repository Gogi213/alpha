#!/bin/bash
# TK-522: сборка ядра в дереве $1 (умолч. /root/p40core-tk522g) на 5.231.25.224 + тест orch (без rsync-моков на боксе) — вывод хвостами
export PATH=$PATH:/root/.cargo/bin
D=${1:-/root/p40core-tk522g}
cd $D/rs || exit 1
touch src/*.rs
cargo build --release 2>&1 | tail -${TAIL:-4}
cd $D && python3 test_orch_l1.py 2>&1 | tail -${TAIL:-4}
