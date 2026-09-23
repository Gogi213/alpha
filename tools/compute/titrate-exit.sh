#!/usr/bin/env bash
# Титрование выхода лонга (план 2026-09-23, G9; владелец 23.09: «отставить шорт и протитровать лонги по
# формам выхода», «добавляем ещё трейлинг-тейк»).
#   titrate-exit.sh <метка>
# Вход — замороженный F10 (подход D20, лестница, срок жизни входа 30 мин); наборы — база лонга
# (`t-bid-age-45`) и две просадки (`t-bid-btc1h-q1`, `t-bid-btc4h-q1`, края v1 — `study/titration-sets-v1.txt`).
# Выход — сетка на каждый стоп:
#   стоп 0.5 / 1 / 2 % (точки прежних сеток) × тейк {1:1; трейл — активация 1R (там, где стоял бы тейк 1:1),
#   откат 0.5R (предложение 23.09, ждёт слова владельца)} × дедлайн 1 / 2 / 4 ч × выход по стене {нет;
#   съели 20 % — число владельца В-80}.
# Трейл привязан к своему стопу, поэтому стопы — отдельными прогонами: иначе сетка скрестила бы
# стоп 0.5 % с трейлом 2 %. Каждая форма — испытание (`--log-trials`). Обе эпохи параллельно.
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
TAG="${1:?метка}"
HIST="${HIST_HOME:-$A/epochs/e-archive}"
LOG="$A/study/titrate-exit-$TAG.log"
say() { echo "== $(date -u +%FT%TZ) titrate-exit $TAG: $*" | tee -a "$LOG"; }

SETS=$(tr ' ' '\n' < "$A/study/titration-sets-v1.txt" \
  | grep -E '^(t-bid-age-45|t-bid-btc1h-q1|t-bid-btc4h-q1):' | tr '\n' ' ')
[ "$(echo "$SETS" | wc -w)" -eq 3 ] || { say "наборы v1 не найдены"; exit 1; }
declare -A TRAIL=([pct0.5]=tr0.5x0.25 [pct1]=tr1x0.5 [pct2]=tr2x1)

for stop in pct0.5 pct1 pct2; do
  exit_grid="--stop-form $stop --take-form 1to1 --take-form ${TRAIL[$stop]} \
    --deadline-secs 3600 --deadline-secs 7200 --deadline-secs 14400 --exit-form none --exit-form eat20"
  say "стоп $stop: 12 форм × 3 набора, история и запись"
  for epoch in "$HIST:2026-09-01" "$A:2026-09-16"; do
    ALPHA_HOME="${epoch%%:*}" FROM_DAY="${epoch##*:}" OOS_DIR="b5/titrx-$TAG-$stop" SETS="$SETS" \
      FORM_EXIT="$exit_grid" FORM_NAME=all VERDICT_FLAGS=--log-trials RUNS=study/runs-2026-09-19.csv \
      GRID_THREADS="${GRID_THREADS:-2}" "$A/bin/oos-frozen.sh" > /dev/null 2>&1 &
  done
  wait
done

python3 "$A/bin/exit-titration-read.py" --tag "titrx-$TAG" --epoch "история=$HIST/study" --epoch "запись=$A/study" \
  --csv "$A/study/titrate-exit-$TAG.csv" > "$A/study/titrate-exit-$TAG.txt" 2>>"$LOG"
say "готово → study/titrate-exit-$TAG.txt"
