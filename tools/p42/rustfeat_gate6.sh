#!/usr/bin/env bash
# TK-609 v6 (на srv — копия рядом с pass: bash rustfeat_gate6.sh): шесть таблиц TK-599 (wa/wb/wc × L-deriv / S-flow), полный `retitr_ftab build` с Rust-эпизодами и Rust-признаками: eps 66 колонок + tk594-таблица + ftab.csv против эталона.
set -uo pipefail
R=/data/p42-tk609; BIN=${BIN:?}; TAG=${TAG:?}
for spec in "wa S fa95205e801e5" "wa L f686957305744" "wb S fa95205e801e5" "wb L f686957305744" "wc S fa95205e801e5" "wc L f686957305744"; do
  set -- $spec
  BIN=$BIN TAG=$TAG W=$1 SIDE=$2 VID=$3 F12=1 PROCS=${PROCS:-3} bash "$(dirname "$0")/rustfeat_pass.sh" 2>&1 | grep -v "^{" 
done
echo "GATE6 done"
