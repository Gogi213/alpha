#!/bin/bash
# TK-500: PGO-сборка ядра (В-32) и гейт «байт в байт» против не-PGO на DOGE 17.09; замер user-времени 3 прогона каждого. Запуск на 5.231.25.224: bash /root/p40core/pgo.sh
export PATH=$PATH:/root/.cargo/bin
cd /root/p40core/rs || exit 1
ARGS="/dev/shm/p40/A /dev/shm/p40/B /dev/shm/p40/TA /dev/shm/p40/TB 12300 12.3 500"
rm -rf /tmp/pgo-data /tmp/pgo-gen /tmp/pgo-use; mkdir -p /tmp/pgo-data
RUSTFLAGS="-Cprofile-generate=/tmp/pgo-data" cargo build --release --target-dir /tmp/pgo-gen 2>&1 | tail -1
nice -n 19 /tmp/pgo-gen/release/p40core /tmp/pgo_train.csv $ARGS 2>&1 | tail -1
/root/.rustup/toolchains/stable-x86_64-unknown-linux-gnu/lib/rustlib/x86_64-unknown-linux-gnu/bin/llvm-profdata merge -o /tmp/pgo-data/merged.profdata /tmp/pgo-data/*.profraw || exit 1
RUSTFLAGS="-Cprofile-use=/tmp/pgo-data/merged.profdata" cargo build --release --target-dir /tmp/pgo-use 2>&1 | tail -1
cargo build --release 2>&1 | tail -1
for v in base pgo; do
  bin=./target/release/p40core; [ $v = pgo ] && bin=/tmp/pgo-use/release/p40core
  for i in 1 2 3; do
    ( TIMEFORMAT="$v run$i user=%U s wall=%R s"; time nice -n 19 $bin /tmp/pgo_$v.csv $ARGS >/dev/null 2>&1 ) 2>&1
  done
done
for f in "" .fills.csv .exits.csv .features.csv .path.csv; do cmp /tmp/pgo_base.csv$f /tmp/pgo_pgo.csv$f && echo "байт в байт: pgo_*.csv$f"; done
sha256sum /tmp/pgo_base.csv* /tmp/pgo_pgo.csv* | cut -c1-12,65-
