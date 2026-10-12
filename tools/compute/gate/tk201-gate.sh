#!/usr/bin/env bash
# TK-201 (В-227, пакет A): гейт «байт в байт» — сборка головы (боевая и с feature r2) на calc и семьи gate-families.sh
# (diff 0 против опорных семьи на проб-сутках). Запуск ТОЛЬКО через alsched:
#   python3 /data/sched/alsched.py submit --cls prod --name tk201-gate --max-runtime 3h --cores 8 --mem 16 -- bash /data/tk201/tk201-gate.sh
# Исходники: /data/tk201/src.tgz (git archive головы, распаковка тут), gate-families.sh рядом.
# Итог: /data/tk201/gate/summary.txt, маркер gate.done.
set -uo pipefail
W=/data/tk201; SRC=$W/src
export RUSTUP_HOME=/opt/rbuild/rustup CARGO_HOME=/opt/rbuild/cargo PATH=/opt/rbuild/cargo/bin:$PATH
mkdir -p "$SRC" && tar -xzf "$W/src.tgz" -C "$SRC" && cd "$SRC" || exit 70
nice -n 5 cargo build --release -j 8 --bin alpha --target-dir "$W/tgt" 2>&1 | tail -3
nice -n 5 cargo build --release -j 8 --features r2 --bin alpha --target-dir "$W/tgt-r2" 2>&1 | tail -3
cp "$W/tgt/release/alpha" "$W/alpha-new" && cp "$W/tgt-r2/release/alpha" "$W/alpha-new-r2" || exit 73
md5sum "$W/alpha-new" "$W/alpha-new-r2" > "$W/bin.md5"
GATE_TAG=201 GATE_DIR=$W/gate NEW_R2=$W/alpha-new-r2 bash "$W/gate-families.sh" "$W/alpha-new"
echo "gate rc=$?"
