#!/bin/bash
# TK-500: предупреждения компилятора ядра на srv (после правки) — показать текст.
export PATH=$PATH:/root/.cargo/bin
cd /root/p40core/rs || exit 1
touch src/main.rs
cargo build --release 2>&1 | grep -A8 "^warning" | head -16
