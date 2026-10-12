#!/bin/bash
# TK-211: те же джобы CI, что `Строгие линты (боевой код)` и `Бюджет линтов` (.github/workflows/quality.yml), на машине сборки.
# Запуск из корня дерева: bash tools/lint-gate.sh <target-dir>. Красное: строгий линт (error) или счёт > .github/lint-budget.txt.
set -o pipefail
TGT="${1:?target-dir}"
L=$(mktemp)
cargo clean -p alpha --target-dir "$TGT" >/dev/null 2>&1   # на тёплом кэше clippy молчит и счёт выходит нулевым
CARGO_TERM_COLOR=never cargo clippy --lib --bins --target-dir "$TGT" --message-format short -- \
  -D clippy::float_cmp -D clippy::panic -D clippy::todo -D clippy::unimplemented -D clippy::dbg_macro \
  -D clippy::mem_forget -D clippy::exit \
  -W clippy::unwrap_used -W clippy::expect_used -W clippy::indexing_slicing \
  -W clippy::cast_possible_truncation -W clippy::cast_precision_loss >"$L" 2>&1
if grep -qE ": error" "$L"; then grep -E ": error" "$L" | head -50; rm -f "$L"; echo "lint-gate: строгие линты красные"; exit 1; fi
got=$(grep -cE ": warning: " "$L"); rm -f "$L"
budget=$(tr -d '[:space:]' < .github/lint-budget.txt)
echo "lint-gate: срабатываний $got, бюджет $budget"
[ "$got" -le "$budget" ] || { echo "lint-gate: долг вырос ($got > $budget)"; exit 1; }
