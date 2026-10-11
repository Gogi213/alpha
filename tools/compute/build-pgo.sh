#!/usr/bin/env bash
# TK-174: боевая сборка головы на calc — x86-64-v3 + PGO (цикл из archive/pgo-cycle.sh, под calc без VPS), один скрипт.
#   build-pgo.sh <тег> <исходники> [plain|gen|train|use|all]     (по умолчанию all = gen -> train -> use)
# Выход: /data/<тег>/ (pgo-raw, merged.profdata, tgt-*), бинарники /opt/alpha-compute/bin/alpha-<тег>-{plain,gen,pgo},
# md5 бинарников и profdata — /data/<тег>/pgo.md5 (в отчёт); маркеры pgo-train.done / pgo-use.done / *.fail.
# plain — v3 без PGO (сторона для perf stat «чистый код»). Обучение — фиксированный набор atom d01 d07, флагов скорости нет
# (они в коде); из env только каталог кэша .abin (конфиг). Тесты v3 не видят: RUSTFLAGS только здесь.
# Запускать юнитом: systemd-run --unit build-pgo-<тег> --collect bash build-pgo.sh <тег> <src>
set -uo pipefail
TAG="${1:?тег}"; SRC="${2:?исходники}"; STEP="${3:-all}"
W=/data/$TAG; BIN=/opt/alpha-compute/bin; RAW=$W/pgo-raw; PD=$W/merged.profdata
TRAIN_MODES="atom d01 d07"; ABIN="${ABIN_TRAIN:-/data/tk051/abin-train}"
PROFDATA_TOOL="${PROFDATA_TOOL:-/data/tk155/llvm-profdata}"; LLVMLIB="${LLVMLIB:-/data/tk155/llvmlib}"
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH
# TK-179: боевой профиль — fat LTO, один codegen-unit, panic=abort (волна −4,7 % стены против v3+PGO, diff 0); только для сборок этого скрипта, тесты не затронуты
export CARGO_PROFILE_RELEASE_LTO=fat CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1 CARGO_PROFILE_RELEASE_PANIC=abort
mkdir -p "$W"
V3='-C target-cpu=x86-64-v3'
build() {  # <rustflags> <target-dir> <бинарь>
  ( cd "$SRC" && RUSTFLAGS="$1" nice -n 5 cargo build --release -j 8 --bin alpha --target-dir "$2" 2>&1 | tail -3 ) || return 1
  install -D "$2/release/alpha" "$3"
}
do_plain() { build "$V3" "$W/tgt-plain" "$BIN/alpha-$TAG-plain" && md5sum "$BIN/alpha-$TAG-plain" >> "$W/pgo.md5"; }
do_gen() { rm -rf "$RAW" "$W/pgo-train.done" "$W/pgo-train.fail"; build "$V3 -C profile-generate=$RAW" "$W/tgt-gen" "$BIN/alpha-$TAG-gen" || { touch "$W/pgo-train.fail"; return 1; }; }
do_train() {
  cd /data/tk051 || return 1
  for m in $TRAIN_MODES; do /data/benchrun.sh stand bash stand.sh "alpha-$TAG-gen" "$m" ALPHA_APPROACH_BIN_DIR="$ABIN" > "$W/pgotrain-$m.out" 2>&1; done
  ls "$RAW"/*.profraw > /dev/null 2>&1 && touch "$W/pgo-train.done" || { touch "$W/pgo-train.fail"; return 1; }
}
do_use() {
  rm -f "$W/pgo-use.done"
  LD_LIBRARY_PATH="$LLVMLIB" "$PROFDATA_TOOL" merge -o "$PD" "$RAW"/*.profraw || return 2
  build "$V3 -C profile-use=$PD -C llvm-args=-pgo-warn-missing-function" "$W/tgt-use" "$BIN/alpha-$TAG-pgo" || return 3
  { md5sum "$PD" "$BIN/alpha-$TAG-pgo"; } >> "$W/pgo.md5"; touch "$W/pgo-use.done"
}
case "$STEP" in
  plain) do_plain ;; gen) do_gen ;; train) do_train ;; use) do_use ;;
  all) do_gen && do_train && do_use ;;
  *) echo "plain|gen|train|use|all"; exit 2 ;;
esac
