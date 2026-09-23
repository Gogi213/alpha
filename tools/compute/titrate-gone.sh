#!/usr/bin/env bash
# Титрование защиты после снятия стены (план 2026-09-23, G9b; владелец 23.09: «если стену сняли и мы в
# позиции — трейл», «сразу выход по снятию — глупость, но снятие — уже риск, нужна защита»).
#   BIN=bin/alpha-<хеш> titrate-gone.sh <метка>
# Вход — замороженный F10; наборы — база лонга и просадки BTC 1 ч / 4 ч (края v1). Выход: стоп 2 % ×
# тейк {1.75 %; трейл 1 % / откат 1 %} × удержание 2 / 4 ч × защита {нет; выход сразу при снятии 90 %;
# трейл после снятия 50 % или 90 % стены с откатом 0.25 / 0.5 / 1 %} = 32 формы × 3 набора.
# Пороги снятия 50/90 % — предложение исполнителя 23.09; откаты — точки трейла G9 v2 (В-87).
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
TAG="${1:?метка}"
HIST="${HIST_HOME:-$A/epochs/e-archive}"
BIN="${BIN:?бинарник с gone<W>tr<T> — BIN=bin/alpha-<хеш>}"
LOG="$A/study/titrate-gone-$TAG.log"
say() { echo "== $(date -u +%FT%TZ) titrate-gone $TAG: $*" | tee -a "$LOG"; }

SETS=$(tr ' ' '\n' < "$A/study/titration-sets-v1.txt" \
  | grep -E '^(t-bid-age-45|t-bid-btc1h-q1|t-bid-btc4h-q1):' | tr '\n' ' ')
[ "$(echo "$SETS" | wc -w)" -eq 3 ] || { say "наборы v1 не найдены"; exit 1; }
gone="--stop-form pct2 --take-form tk1.75 --take-form tr1x1 --deadline-secs 7200 --deadline-secs 14400"
for x in none gone90 gone50tr0.25 gone50tr0.5 gone50tr1 gone90tr0.25 gone90tr0.5 gone90tr1; do
  gone="$gone --exit-form $x"
done

say "32 формы × 3 набора, история и запись, $BIN"
for epoch in "$HIST:2026-09-01" "$A:2026-09-16"; do
  ALPHA_HOME="${epoch%%:*}" FROM_DAY="${epoch##*:}" OOS_DIR="b5/titrg-$TAG" SETS="$SETS" BIN="$BIN" \
    FORM_EXIT="$gone" FORM_NAME=all VERDICT_FLAGS=--log-trials RUNS=study/runs-2026-09-19.csv \
    GRID_THREADS="${GRID_THREADS:-2}" DAY_JOBS="${DAY_JOBS:-4}" "$A/bin/oos-frozen.sh" > /dev/null 2>&1 &
done
wait
python3 "$A/bin/exit-titration-read.py" --tag "titrg" --epoch "история=$HIST/study" --epoch "запись=$A/study" \
  --csv "$A/study/titrate-gone-$TAG.csv" > "$A/study/titrate-gone-$TAG.txt" 2>>"$LOG"
say "готово → study/titrate-gone-$TAG.txt"
