#!/usr/bin/env bash
# Титрование живой формы F10 по режиму рынка и возрасту стены, лонг и шорт (план 2026-09-23, G4/G5).
#   titrate.sh <метка> [сутки записи до]
# 1) точки — квинтили режима по минутам «истории» (titration-points.py), 52 набора;
# 2) замороженная форма F10 с этими наборами на «истории» (01–15.09) и на записи (с 16.09) — обе эпохи
#    разом, сутки параллельно (oos-frozen.sh, SETS, --log-trials: корзина — испытание журнала);
# 3) сводка обеих эпох рядом → study/titration-<метка>.{txt,csv}.
# WAIT_NIGHT=<сутки> — сначала дождаться конца регулярной ночи этих суток (строка «nightly done»).
set -uo pipefail
TAG="${1:?метка}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_env.sh
source "$SELF_DIR/_env.sh"
A="${ALPHA_BASE:-$HOME/alpha}"
HIST="${HIST_HOME:-$A/epochs/e-archive}"
LOG="$A/study/titration-$TAG.log"
say() { echo "== $(date -u +%FT%TZ) titrate $TAG: $*" | tee -a "$LOG"; }

if [ -n "${WAIT_NIGHT:-}" ]; then
  say "жду конца ночи $WAIT_NIGHT"
  until grep -q "nightly done" "$A/study/nightly-$WAIT_NIGHT.log" 2>/dev/null; do sleep 120; done
fi

# Наборы метки замораживаются первым прогоном: дочёт новых суток той же меткой обязан идти теми же
# корзинами, иначе сутки одной метки смешают разные края.
SETS_FILE="$A/study/titration-sets-$TAG.txt"
if [ -s "$SETS_FILE" ]; then
  SETS=$(cat "$SETS_FILE")
else
  SETS=$(python3 "$A/bin/titration-points.py" --regime "$HIST/study/regime" --from 2026-09-01 --to 2026-09-15 \
    --out "$A/study/titration-points-$TAG.csv" 2>>"$LOG")
  [ -n "$SETS" ] || { say "точки не посчитались"; exit 1; }
  echo "$SETS" > "$SETS_FILE"
fi
say "наборов $(echo "$SETS" | wc -w)"

run_epoch() {
  local home=$1 from=$2 errlog=$3
  ALPHA_HOME="$home" FROM_DAY="$from" OOS_DIR="b5/titr-$TAG" SETS="$SETS" VERDICT_FLAGS=--log-trials \
    RUNS="${RUNS:-$RUNS_JOURNAL}" GRID_THREADS="${GRID_THREADS:-2}" "$A/bin/oos-frozen.sh" > "$errlog" 2>&1
}
say "история и запись"
ERR_HIST="$A/study/titration-$TAG-$(basename "$HIST").err"
ERR_REC="$A/study/titration-$TAG-$(basename "$A").err"
run_epoch "$HIST" 2026-09-01 "$ERR_HIST" & p1=$!
run_epoch "$A" 2026-09-16 "$ERR_REC" & p2=$!
fail=0
wait "$p1" || { fail=$((fail + 1)); say "ОШИБКА: эпоха история не завершилась — $ERR_HIST"; }
wait "$p2" || { fail=$((fail + 1)); say "ОШИБКА: эпоха запись не завершилась — $ERR_REC"; }
[ "$fail" -eq 0 ] || say "$fail из 2 эпох провалились"

python3 "$A/bin/titration-read.py" --tag "titr-$TAG" --epoch "история=$HIST/study" --epoch "запись=$A/study" \
  --csv "$A/study/titration-$TAG.csv" > "$A/study/titration-$TAG.txt" 2>>"$LOG"
say "готово → study/titration-$TAG.txt"
