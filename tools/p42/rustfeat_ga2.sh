#!/usr/bin/env bash
# TK-609 G-A2: полное окно U0 (46 046 эп., база 2026-09-01) и август (25 281 эп., база 2026-07-18) — Rust против эталонных таблиц TK-594, каждая строка, побитово.
# Использование: ga2.sh <u0|aug> <flow|deriv> [threads]. Эталон только читается; выход — /data/p42-tk609/ga2/<окно>-<режим>.parquet, отчёт gate-*.json (+ классы краёв).
set -uo pipefail
WIN=${1:?u0|aug}; MODE=${2:?flow|deriv}; TH=${3:-2}
BIN=${BIN:-/data/p42-tk609/bin/alpha-feat}; PY=${PY:-/data/p42-tk609/py}; OUT=${OUT:-/data/p42-tk609/ga2}; mkdir -p "$OUT"
case "$WIN" in
  u0)  EPS=/data/tk594/ep_u0.parquet; REF=/data/tk594/feat_$MODE.parquet; BASE=2026-09-01 ;;
  aug) EPS=/data/tk594/aug_u0.parquet; REF=/data/tk594/aug_feat_$MODE.parquet; BASE=2026-07-18; export TK594_TRADES=/data/p42-past/aggtrades ;;
esac
echo "== $WIN $MODE eps=$EPS ref=$REF ref_md5=$(md5sum "$REF" | cut -c1-12) tk594_feat.py=$(md5sum /data/tk594/py/tk594_feat.py | cut -c1-12)"
python3 "$PY/rustfeat_run.py" "$EPS" "$OUT/$WIN-$MODE.parquet" --mode "$MODE" --base-date "$BASE" --procs "$TH" --bin "$BIN" 2>&1 | tail -2
python3 "$PY/rustfeat_gate.py" compare "$REF" "$OUT/$WIN-$MODE.parquet" --cols "$MODE" --out "$OUT/gate-$WIN-$MODE.json" --eps "$EPS" --base-date "$BASE"; rc=$?
echo "GA2 $WIN $MODE rc=$rc"; echo $rc > "$OUT/rc-$WIN-$MODE"
