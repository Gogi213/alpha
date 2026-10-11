#!/usr/bin/env bash
# Титрование выхода лонга (план 2026-09-23, G9; владелец 23.09: «отставить шорт и протитровать лонги по
# формам выхода», «добавляем ещё трейлинг-тейк», «сделай чуть меньше шаг для стопа и тейка и несколько
# вариаций для трейлинга, чтобы подобрать лучший»). Тонкая обёртка над `titrate-forms.sh` (2026-09-23,
# общий каркас с titrate-gone.sh/titrate-be.sh): три профиля выхода по очереди, каждый — обе эпохи
# параллельно (`b5/titrx-<метка>-<профиль>`, лог `study/titrate-exit-<метка>.log`):
#   fix   — стоп × тейк раздельно, шаг 0.25 % (147 форм);
#   trail — трейл вместо тейка, три стопа × 11 вариантов трейла (99 форм);
#   wall  — выход «съели 20 %» (В-80) (6 форм).
# Формы и точки — слова владельца 23.09 (В-87). Наборы — база лонга (`t-bid-age-45`) и две просадки
# (`t-bid-btc1h-q1`, `t-bid-btc4h-q1`, края v1 — `study/titration-sets-v1.txt`). Каждая форма —
# испытание (`--log-trials`).
#   titrate-exit.sh <метка>
set -uo pipefail
TAG="${1:?метка}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
A="${ALPHA_BASE:-$HOME/alpha}"
HIST="${HIST_HOME:-$A/epochs/e-archive}"
LOG="$A/study/titrate-exit-$TAG.log"

fail=0
for run in fix trail wall; do
  "$SELF_DIR/titrate-forms.sh" "$run" "$TAG" || fail=1
done
python3 "$A/bin/exit-titration-read.py" --tag "titrx-$TAG" --epoch "история=$HIST/study" --epoch "запись=$A/study" \
  --csv "$A/study/titrate-exit-$TAG.csv" > "$A/study/titrate-exit-$TAG.txt" 2>>"$LOG"
echo "== $(date -u +%FT%TZ) titrate-exit $TAG: готово → study/titrate-exit-$TAG.txt" | tee -a "$LOG"
exit "$fail"
