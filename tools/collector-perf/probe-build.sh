#!/bin/bash
# Сборка пробы uring_ts_probe на VPS Софии (под замком сборок; цель удаляется sweep.sh). Запуск на Софии: bash probe-build.sh
export HOME=/root PATH=/root/.cargo/bin:$PATH
D=/opt/alpha-compute
flock -w 3600 $D/.build.lock $D/sweep.sh run $D/target-probe bash -c "cd $D/perf-probe/uring_ts_probe && cargo build --release --target-dir $D/target-probe 2>&1 | tail -30 && install -D $D/target-probe/release/uring_ts_probe $D/perf/uring_ts_probe && ls -la $D/perf/uring_ts_probe"
