#!/bin/bash
# TK-617 v3: сетка жёсткого стопа на базах L_base / S_base, окна wa / wb / wc одним вызовом движка на окно (как short_levers_run.sh TK-618).
# Запуск на srv: systemd-run --unit=tk617-stopgrid --nice=10 -p CPUQuota=400% bash /data/p42-tk617/stopgrid/tk617_stopgrid_run.sh
set -u
ROOT=/data/p42-tk617/stopgrid
BIN=/data/p42-tk599/bin/alpha-master
VAR=$ROOT/variants.txt
SYMS="$(cat /data/p42-t566/symbols.txt)"
mkdir -p "$ROOT/log" "$ROOT/sig-cache"
: > "$ROOT/run.rc"
declare -A T0=( [wa]=2026-06-17 [wb]=2026-07-25 [wc]=2026-09-01 )   # = retitr_full.WINDOWS
for w in wa wb wc; do
  mkdir -p "$ROOT/out/$w"
  P42_T0=${T0[$w]} "$BIN" breakout tick --cache "/data/p42-tk599/$w/cache" --trades "/data/p42-tk599/$w/aggtrades" \
    --variants "$VAR" --symbols "$SYMS" --out "$ROOT/out/$w" --threads 4 \
    --tick-store /data/p42-tk599/tick-store --tick-store-cap-gb 3 --tick-store-min-free-gb 5 \
    --sig-cache "$ROOT/sig-cache" >> "$ROOT/log/run-$w.log" 2>&1
  rc=$?
  echo "$w $rc" >> "$ROOT/run.rc"
  if [ "$rc" -ne 0 ]; then exit "$rc"; fi
done
echo done >> "$ROOT/run.rc"
