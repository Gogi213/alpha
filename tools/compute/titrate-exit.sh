#!/usr/bin/env bash
# Титрование выхода лонга (план 2026-09-23, G9; владелец 23.09: «отставить шорт и протитровать лонги по
# формам выхода», «добавляем ещё трейлинг-тейк», «сделай чуть меньше шаг для стопа и тейка и несколько
# вариаций для трейлинга, чтобы подобрать лучший»).
#   titrate-exit.sh <метка>
# Вход — замороженный F10 (подход D20, лестница, срок жизни входа 30 мин); наборы — база лонга
# (`t-bid-age-45`) и две просадки (`t-bid-btc1h-q1`, `t-bid-btc4h-q1`, края v1 — `study/titration-sets-v1.txt`).
# Три прогона выхода, у каждого свой каталог (`b5/titrx-<метка>-<прогон>`):
#   fix   — стоп × тейк раздельно, шаг 0.25 %: стоп 0.5…2 % × тейк 0.5…2 % × дедлайн 1 / 2 / 4 ч (147 форм);
#   trail — трейл вместо тейка: активация 0.5 / 1 / 1.5 / 2 % × откат 0.25 / 0.5 / 1 % (откат ≤ активации, 11 вариантов)
#           × стоп 1 / 1.5 / 2 % × дедлайн 1 / 2 / 4 ч (99 форм);
#   wall  — выход «съели 20 %» (В-80) при стопе 1 / 2 % и тейке 1:1 × дедлайн 1 / 2 / 4 ч (6 форм).
# Шаг и точки — слова владельца 23.09 (В-87); трейл — на трёх стопах, а не на семи, ради времени счёта
# (замер 23.09: ~0.05 с на форму и монету-сутки). Каждая форма — испытание (`--log-trials`). Эпохи параллельно.
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
TAG="${1:?метка}"
# TRIALS="" — пересчёт тех же испытаний (исправление бэктеста, 23.09 полночь), не новые: в журнал не пишутся.
HIST="${HIST_HOME:-$A/epochs/e-archive}"
LOG="$A/study/titrate-exit-$TAG.log"
say() { echo "== $(date -u +%FT%TZ) titrate-exit $TAG: $*" | tee -a "$LOG"; }

SETS=$(tr ' ' '\n' < "$A/study/titration-sets-v1.txt" \
  | grep -E '^(t-bid-age-45|t-bid-btc1h-q1|t-bid-btc4h-q1):' | tr '\n' ' ')
[ "$(echo "$SETS" | wc -w)" -eq 3 ] || { say "наборы v1 не найдены"; exit 1; }

DL="--deadline-secs 3600 --deadline-secs 7200 --deadline-secs 14400"
fix="$DL --exit-form none"
for x in 0.5 0.75 1 1.25 1.5 1.75 2; do fix="$fix --stop-form pct$x --take-form tk$x"; done
trail="$DL --exit-form none --stop-form pct1 --stop-form pct1.5 --stop-form pct2"
for t in tr0.5x0.25 tr0.5x0.5 tr1x0.25 tr1x0.5 tr1x1 tr1.5x0.25 tr1.5x0.5 tr1.5x1 tr2x0.25 tr2x0.5 tr2x1; do
  trail="$trail --take-form $t"
done
wall="$DL --exit-form eat20 --stop-form pct1 --stop-form pct2 --take-form 1to1"

for run in fix trail wall; do
  say "прогон $run: история и запись"
  for epoch in "$HIST:2026-09-01" "$A:2026-09-16"; do
    ALPHA_HOME="${epoch%%:*}" FROM_DAY="${epoch##*:}" OOS_DIR="b5/titrx-$TAG-$run" SETS="$SETS" \
      FORM_EXIT="${!run}" FORM_NAME=all VERDICT_FLAGS="${TRIALS---log-trials}" RUNS=study/runs-2026-09-19.csv \
      GRID_THREADS="${GRID_THREADS:-2}" "$A/bin/oos-frozen.sh" > /dev/null 2>&1 &
  done
  wait
done

python3 "$A/bin/exit-titration-read.py" --tag "titrx-$TAG" --epoch "история=$HIST/study" --epoch "запись=$A/study" \
  --csv "$A/study/titrate-exit-$TAG.csv" > "$A/study/titrate-exit-$TAG.txt" 2>>"$LOG"
say "готово → study/titrate-exit-$TAG.txt"
