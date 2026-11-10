#!/bin/bash
# TK-525: сборка pq2bin с P40_L1_STRICT в /root/pq2bin-tk525 на 5.231.25.224 (копия tk522e; main.rs лежит в /tmp) — вывод хвостом
export PATH=$PATH:/root/.cargo/bin
D=/root/pq2bin-tk525
if [ ! -d $D ]; then
  mkdir $D && cd /root/pq2bin-tk522e && cp -r Cargo.toml Cargo.lock src target $D/ || exit 1
fi
cp /tmp/main.rs $D/src/ || exit 1
cd $D || exit 1
touch src/*.rs
nice -n 5 cargo build --release 2>&1 | tail -3
