#!/bin/bash
# TK-500: тесты и релизная сборка ядра на 5.231.25.224 (запуск: ssh ... 'bash /root/p40core/rs/ct.sh')
export PATH=$PATH:/root/.cargo/bin
cd /root/p40core/rs || exit 1
nice -n 19 cargo test --release 2>&1 | tail -25
nice -n 19 cargo build --release 2>&1 | tail -3
