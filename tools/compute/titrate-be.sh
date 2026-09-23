#!/usr/bin/env bash
# Титрование безубытка после снятия стены (план 2026-09-23, G9c; владелец 23.09: «защита брекивеном, если сняли:
# снятие — стоп в ноль, далее, если уже идёт к тейку, включается базовый трейлинг»). Тонкая обёртка
# над `titrate-forms.sh` (2026-09-23, общий каркас с titrate-exit.sh/titrate-gone.sh).
#   BIN=bin/alpha-<хеш> titrate-be.sh <метка>
# Вход — замороженный F10; наборы — база лонга и просадки BTC 1 ч / 4 ч (края v1). Выход: стоп 2 % ×
# тейк {трейл 1 % / откат 1 %; ранний трейл 0.5 % / 0.25 %; тейк 1.75 %} × удержание 2 / 4 ч × защита {нет; безубыток
# после снятия 50 % / 90 % стены — мягкий `be` и жёсткий `bex`} = 30 форм × 3 набора
# (`b5/titrb-<метка>`, лог `study/titrate-be-<метка>.log`).
set -uo pipefail
TAG="${1:?метка}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
A="${ALPHA_BASE:-$HOME/alpha}"
HIST="${HIST_HOME:-$A/epochs/e-archive}"
: "${BIN:?бинарник с gone<W>tr<T> — BIN=bin/alpha-<хеш>}"
LOG="$A/study/titrate-be-$TAG.log"

fail=0
"$SELF_DIR/titrate-forms.sh" be "$TAG" || fail=1
python3 "$A/bin/exit-titration-read.py" --tag "titrb" --epoch "история=$HIST/study" --epoch "запись=$A/study" \
  --csv "$A/study/titrate-be-$TAG.csv" > "$A/study/titrate-be-$TAG.txt" 2>>"$LOG"
echo "== $(date -u +%FT%TZ) titrate-be $TAG: готово → study/titrate-be-$TAG.txt" | tee -a "$LOG"
exit "$fail"
