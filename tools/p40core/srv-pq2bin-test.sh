#!/bin/bash
# TK-522 (E-021): тесты и сборка pq2bin в копии дерева /root/pq2bin-tk522e на 5.231.25.224 (l1.rs, main.rs уже в /tmp) — вывод хвостами
export PATH=$PATH:/root/.cargo/bin
D=/root/pq2bin-tk522e
if [ ! -d $D ]; then
  mkdir $D && cd /root/p40core-tk522/pq2bin-src && cp -r Cargo.toml Cargo.lock src target $D/ || exit 1
fi
cp /tmp/l1.rs /tmp/main.rs $D/src/ || exit 1
cd $D || exit 1
touch src/*.rs
nice -n 5 cargo test --release 2>&1 | tail -${TAIL:-8}
nice -n 5 cargo build --release 2>&1 | tail -3
