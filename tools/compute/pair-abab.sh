#!/usr/bin/env bash
# TK-174: честная пара волн b14 01–15.01 в ОДНОЙ сессии benchrun, чередование ABAB(C): стена, iowait, ЦП-с, инструкции, IPC, гейт.
#   /data/benchrun.sh wave bash pair-abab.sh <тег> <обёртка:метка>...   (обёртка — имя в /opt/alpha-compute/bin)
# Выход: /data/<тег>/pair.txt. Волна — как в TK-155 (tk048-orch-grp16q.sh, 8 потоков, CPUQuota 1500 %); флагов скорости env нет.
TAG="${1:?тег}"; shift
D15=$(seq -s, -f "2026-01-%02g" 1 15)
OUT=/data/$TAG/pair.txt; : > "$OUT"
for r in "$@" "$@"; do
  bin=${r%%:*}; nm=${r##*:}; run=$nm-$RANDOM
  systemd-run --wait --collect --unit "$TAG-p$run" -p CPUQuota=1500% \
    --setenv=QTOP_FROM=/data/tk048/units-q15b14ac.txt --setenv=QTOP_N=120 --setenv=HOIST_PRED=/data/tk048/pred-q15.txt \
    --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=HOIST_S=40 --setenv=WARM_BG=1 \
    --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 \
    --setenv=PREWARM_SMALL=1 --setenv=EVENTS_WIDE=1 \
    bash /data/tk048/tk048-orch-grp16q.sh "p$run" "$bin" "$D15" 8 20 > /dev/null 2>&1
  { echo "== $nm $bin"; grep -E "wall_s|iowait|cpu_busy|read_GB|perf_instructions|gate" "/data/tk048/orch-p$run.out/metrics.txt"; } >> "$OUT"
done
touch "/data/$TAG/pair.done"
