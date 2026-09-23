#!/usr/bin/env bash
# Титрование защиты после снятия стены (план 2026-09-23, G9b; владелец 23.09: «если стену сняли и мы в
# позиции — трейл», «сразу выход по снятию — глупость, но снятие — уже риск, нужна защита»). Тонкая
# обёртка над `titrate-forms.sh` (2026-09-23, общий каркас с titrate-exit.sh/titrate-be.sh).
#   BIN=bin/alpha-<хеш> titrate-gone.sh <метка>
# Вход — замороженный F10; наборы — база лонга и просадки BTC 1 ч / 4 ч (края v1). Выход: стоп 2 % ×
# тейк {1.75 %; трейл 1 % / откат 1 %} × удержание 2 / 4 ч × защита {нет; выход сразу при снятии 90 %;
# трейл после снятия 50 % или 90 % стены с откатом 0.25 / 0.5 / 1 %} = 32 формы × 3 набора
# (`b5/titrg-<метка>`, лог `study/titrate-gone-<метка>.log`).
# Пороги снятия 50/90 % — предложение исполнителя 23.09; откаты — точки трейла G9 v2 (В-87).
set -uo pipefail
TAG="${1:?метка}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
A="${ALPHA_BASE:-$HOME/alpha}"
HIST="${HIST_HOME:-$A/epochs/e-archive}"
: "${BIN:?бинарник с gone<W>tr<T> — BIN=bin/alpha-<хеш>}"
LOG="$A/study/titrate-gone-$TAG.log"

fail=0
"$SELF_DIR/titrate-forms.sh" gone "$TAG" || fail=1
python3 "$A/bin/exit-titration-read.py" --tag "titrg" --epoch "история=$HIST/study" --epoch "запись=$A/study" \
  --csv "$A/study/titrate-gone-$TAG.csv" > "$A/study/titrate-gone-$TAG.txt" 2>>"$LOG"
echo "== $(date -u +%FT%TZ) titrate-gone $TAG: готово → study/titrate-gone-$TAG.txt" | tee -a "$LOG"
exit "$fail"
