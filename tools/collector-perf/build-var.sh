#!/bin/bash
# Сборка вариантов бинаря на VPS Софии под замком сборок: build-var.sh <имя> [фичи cargo] [RUSTFLAGS]; исходники — /opt/alpha-compute/perf-src.tgz.
# Результат: /opt/alpha-compute/perf/alpha-<имя> (md5 в perf/build-<имя>.log).
export HOME=/root PATH=/root/.cargo/bin:$PATH
D=/opt/alpha-compute
NAME=$1; FEAT=${2:-}; RF=${3:-}
SRC=$D/perf-src-$NAME
rm -rf $SRC; mkdir -p $SRC; tar -xzf $D/perf-src.tgz -C $SRC
mkdir -p $D/perf
flock -w 7200 $D/.build.lock $D/sweep.sh run $D/target-perf-$NAME bash -c "cd $SRC && RUSTFLAGS='$RF' ALPHA_COMMIT=$NAME cargo build --release ${FEAT:+--features $FEAT} --target-dir $D/target-perf-$NAME 2>&1 | tail -3 && install -D $D/target-perf-$NAME/release/alpha $D/perf/alpha-$NAME && md5sum $D/perf/alpha-$NAME" > $D/perf/build-$NAME.log 2>&1
rm -rf $SRC
