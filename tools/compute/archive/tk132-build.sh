#!/usr/bin/env bash
# TK-132: бинарник alpha-tk132p1 (ветка tk132-p1, без r2) на VPS из архива wave2tk132.tgz — для гейта d15/d01 diff 0.
set -uo pipefail
N=tk132p1
export HOME=/root CARGO_TARGET_DIR=/opt/alpha-compute/target-$N RUSTFLAGS='-C target-cpu=x86-64-v3 '
SRC=/opt/alpha-compute/wave2-src$N
rm -f /opt/alpha-compute/$N.done /opt/alpha-compute/$N.fail
rm -rf "$SRC" && mkdir -p "$SRC" && tar -xzf /opt/alpha-compute/wave2tk132.tgz -C "$SRC" && cd "$SRC" || { touch /opt/alpha-compute/$N.fail; exit 1; }
if flock -w 14400 /opt/alpha-compute/.build.lock /opt/alpha-compute/sweep.sh run "$CARGO_TARGET_DIR" bash -c "nice -n 5 /root/.cargo/bin/cargo build --release -j 3 --bin alpha && cp $CARGO_TARGET_DIR/release/alpha /opt/alpha-compute/bin/alpha-$N" >/opt/alpha-compute/$N.log 2>&1 && md5sum /opt/alpha-compute/bin/alpha-$N > /opt/alpha-compute/$N.md5; then
  touch /opt/alpha-compute/$N.done
else
  touch /opt/alpha-compute/$N.fail
fi
