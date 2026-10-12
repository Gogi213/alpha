#!/usr/bin/env bash
# TK-174: гейт r2-семей (r2, tk115*) — сборка головы с feature r2 (v3, без PGO: выходы от PGO не зависят) и gate-families.sh.
#   gate-r2.sh <тег> <исходники> <боевой бинарь головы> <семьи>
# Выход: /opt/alpha-compute/bin/alpha-<тег>-r2, /data/<тег>/gate2.out, маркер /data/<тег>/r2.done. Запуск — только через alsched.
set -uo pipefail
TAG=${1:?тег}; SRC=${2:?исходники}; PGO=${3:?бинарь}; FAMS=${4:?семьи}; W=/data/$TAG
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH
rm -f "$W/r2.done" "/opt/alpha-compute/bin/alpha-$TAG-r2"   # TK-219 п.11: прежний бинарь тега удалён до сборки — при сбое гейт не пойдёт на старом
( cd "$SRC" && RUSTFLAGS="-C target-cpu=x86-64-v3" cargo build --release -j 8 --features r2 --bin alpha --target-dir "$W/tgt-r2" 2>&1 | tail -3 ) || exit 1
install -D "$W/tgt-r2/release/alpha" "/opt/alpha-compute/bin/alpha-$TAG-r2" || exit 1
GATE_TAG=${TAG}2 NEW_R2="/opt/alpha-compute/bin/alpha-$TAG-r2" bash "$(dirname "$0")/gate-families.sh" "$PGO" "$FAMS" > "$W/gate2.out" 2>&1
touch "$W/r2.done"
