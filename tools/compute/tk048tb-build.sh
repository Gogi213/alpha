#!/bin/bash
# Сборка (без PGO) бинарника с ALPHA_TOUCH_BIN на VPS: alpha-b16tbin; архив — /opt/alpha-compute/tk048tb.tgz
set -uo pipefail
export HOME=/root CARGO_TARGET_DIR=/opt/alpha-compute/target-tk048tb RUSTFLAGS='-C target-cpu=x86-64-v3 '
SRC=/opt/alpha-compute/wave2-srctk048tb
rm -rf $SRC && mkdir -p $SRC && tar -xzf /opt/alpha-compute/tk048tb.tgz -C $SRC && cd $SRC || { touch /opt/alpha-compute/tk048tb.fail; exit 1; }
if flock -w 14400 /opt/alpha-compute/.build.lock /opt/alpha-compute/sweep.sh run $CARGO_TARGET_DIR bash -c 'nice -n 5 /root/.cargo/bin/cargo build --release -j 3 --bin alpha && cp $CARGO_TARGET_DIR/release/alpha /opt/alpha-compute/bin/alpha-b16tbin' >/opt/alpha-compute/tk048tb.log 2>&1 && md5sum /opt/alpha-compute/bin/alpha-b16tbin > /opt/alpha-compute/tk048tb.md5; then touch /opt/alpha-compute/tk048tb.done; else touch /opt/alpha-compute/tk048tb.fail; fi
