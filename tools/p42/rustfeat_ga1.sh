#!/usr/bin/env bash
# TK-609 G-A1: шесть готовых таблиц TK-599 (wa/wb/wc × L-deriv / S-flow) — Rust против эталона Python, побитово.
# srv; окружение источников — как у ftab TK-599 (TK594_TRADES=<окно>/aggtrades, TK594_METRICS=metrics-all, остальное по умолчанию).
set -uo pipefail
BIN=${BIN:-/data/p42-tk609/bin/alpha-feat}; PY=${PY:-/data/p42-tk609/py}; OUT=${OUT:-/data/p42-tk609/ga1}
mkdir -p "$OUT"; rc=0
declare -A BD=([wa]=2026-06-17 [wb]=2026-07-25 [wc]=2026-09-01)
for w in wa wb wc; do
  export TK594_TRADES=/data/p42-tk599/$w/aggtrades TK594_METRICS=/data/p42-tk599/metrics-all
  for spec in "f686957305744 L deriv" "fa95205e801e5 S flow"; do
    set -- $spec; d=/data/p42-tk599/ftab/$w/$1; ref=$d/tk594-$3-$2.parquet
    echo "== $w $2 $3 ref=$(md5sum "$ref" | cut -c1-12)"
    nice -n 19 python3 "$PY/rustfeat_run.py" "$d/eps-$2.parquet" "$OUT/$w-$2.parquet" --mode "$3" --base-date "${BD[$w]}" --procs 4 --bin "$BIN" 2>&1 | tail -2
    python3 "$PY/rustfeat_gate.py" compare "$ref" "$OUT/$w-$2.parquet" --cols "$3" --out "$OUT/gate-$w-$2.json" --eps "$d/eps-$2.parquet" --base-date "${BD[$w]}" || rc=1
  done
done
echo "GA1 rc=$rc"; echo $rc > "$OUT/rc"
