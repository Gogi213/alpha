#!/usr/bin/env bash
# Титрование безубытка после снятия стены (план 2026-09-23, G9c; владелец 23.09: «защита брекивеном, если сняли:
# снятие — стоп в ноль, далее, если уже идёт к тейку, включается базовый трейлинг»).
#   BIN=bin/alpha-<хеш> titrate-be.sh <метка>
# Вход — замороженный F10; наборы — база лонга и просадки BTC 1 ч / 4 ч (края v1). Выход: стоп 2 % ×
# тейк {трейл 1 % / откат 1 %; ранний трейл 0.5 % / 0.25 %; тейк 1.75 %} × удержание 2 / 4 ч × защита {нет; безубыток
# после снятия 50 % / 90 % стены — мягкий `be` и жёсткий `bex`} = 30 форм × 3 набора.
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
TAG="${1:?метка}"
# TRIALS="" — пересчёт тех же испытаний (исправление бэктеста, 23.09 полночь), не новые: в журнал не пишутся.
HIST="${HIST_HOME:-$A/epochs/e-archive}"
BIN="${BIN:?бинарник с gone<W>tr<T> — BIN=bin/alpha-<хеш>}"
LOG="$A/study/titrate-be-$TAG.log"
say() { echo "== $(date -u +%FT%TZ) titrate-be $TAG: $*" | tee -a "$LOG"; }

SETS=$(tr ' ' '\n' < "$A/study/titration-sets-v1.txt" \
  | grep -E '^(t-bid-age-45|t-bid-btc1h-q1|t-bid-btc4h-q1):' | tr '\n' ' ')
[ "$(echo "$SETS" | wc -w)" -eq 3 ] || { say "наборы v1 не найдены"; exit 1; }
gone="--stop-form pct2 --take-form tr1x1 --take-form tr0.5x0.25 --take-form tk1.75 --deadline-secs 7200 --deadline-secs 14400"
for x in none gone50be gone50bex gone90be gone90bex; do
  gone="$gone --exit-form $x"
done

say "30 форм × 3 набора, история и запись, $BIN"
for epoch in "$HIST:2026-09-01" "$A:2026-09-16"; do
  ALPHA_HOME="${epoch%%:*}" FROM_DAY="${epoch##*:}" OOS_DIR="b5/titrb-$TAG" SETS="$SETS" BIN="$BIN" \
    FORM_EXIT="$gone" FORM_NAME=all VERDICT_FLAGS="${TRIALS---log-trials}" RUNS=study/runs-2026-09-19.csv \
    GRID_THREADS="${GRID_THREADS:-2}" DAY_JOBS="${DAY_JOBS:-4}" "$A/bin/oos-frozen.sh" > /dev/null 2>&1 &
done
wait
python3 "$A/bin/exit-titration-read.py" --tag "titrb" --epoch "история=$HIST/study" --epoch "запись=$A/study" \
  --csv "$A/study/titrate-be-$TAG.csv" > "$A/study/titrate-be-$TAG.txt" 2>>"$LOG"
say "готово → study/titrate-be-$TAG.txt"
