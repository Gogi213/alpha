#!/bin/bash
# TK-618, П-42 §13.30 п. 8.3: 34 клетки шорта + контроль (S_base, S_rf_0.3) на окнах wa / wb / wc одним вызовом движка на окно.
# Запуск на srv: systemd-run --unit=tk618-run --nice=19 --property=CPUWeight=1 bash /data/p42-tk618/code/tools/p42/short_levers_run.sh
# Окно за окном; код возврата окна — в $ROOT/run.rc; любой ненулевой — остановка. Сутки позже 13.11 в окнах не лежат (данные до 13.11).
set -u
ROOT=/data/p42-tk618
BIN=/data/p42-tk599/bin/alpha-master
VAR=$ROOT/code/docs/research/freeze/tk618-variants.txt
SYMS="$(cat /data/p42-t566/symbols.txt)"
mkdir -p "$ROOT/log" "$ROOT/sig-cache"
: > "$ROOT/run.rc"
declare -A T0=( [wa]=2026-06-17 [wb]=2026-07-25 [wc]=2026-09-01 )   # = retitr_full.WINDOWS (проверяется до запуска)
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
