#!/usr/bin/env bash
# Ось стороны (этап 1 docs/plan/alpha-roadmap-2026-09-19.md; намёк — side-asymmetry-2026-09-19.md):
# семьи флоров a45 (возраст ≥ 2700 с) и s100 (сила ×поток ≥ 100 %) × сторона bid|ask × 32 формы
# базы В-65 = 128 испытаний (предрегистрация — строка prereg в runs.csv ДО запуска). Сетки идут
# последовательно на всех сутках корня (или окне DAYS_WINDOW, как у nightly-grid.sh), после каждой —
# вердикт; испытания регистрируются в журнале один раз на вид сетки (маркер study/.trials-logged-<kind>),
# повторные прогоны по новым суткам журнал не раздувают. Запуск на счётной машине:
#   systemd-run --unit alpha-chain-side -p WorkingDirectory=/opt/alpha-compute \
#     -p StandardOutput=append:/opt/alpha-compute/study/chain-side.log \
#     -p StandardError=append:/opt/alpha-compute/study/chain-side.log /opt/alpha-compute/bin/side-grid.sh
# Артефакты: b5/side-<день>-<семья>-<сторона>/, study/bounce-verdict-side-<день>-*.csv, лог study/chain-side.log.
set -uo pipefail
cd /opt/alpha-compute || exit 1
DAY=$(date -u +%F)
LOG=study/chain-side.log
BIN=/opt/alpha-compute/bin/alpha
RUNS=study/runs-2026-09-19.csv
DAYS_WINDOW="${DAYS_WINDOW:-}"
DAYS_ALL=$(ls root/*.binlog* 2>/dev/null | sed -E 's/.*-([0-9]{4}-[0-9]{2}-[0-9]{2}).*/\1/' | sort -u)
DAY_ARGS=""
if [ -n "$DAYS_WINDOW" ]; then
  for d in $(echo "$DAYS_ALL" | tail -n "$DAYS_WINDOW"); do DAY_ARGS="$DAY_ARGS --day $d"; done
fi
if systemctl list-units "alpha-grid-*" --no-legend | grep -q running; then
  echo "== $(date -u +%FT%TZ) сетка ещё идёт — ось стороны не запущена" >> "$LOG"; exit 0
fi
USD="--h3-mode notional --h3-usd 10000"
BASE="--stop-form before --stop-form at --stop-form behind --stop-form midfr --stop-form stack2 --stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form 1to1"
run_one() {
  local kind=$1; shift
  local label="side-$DAY-$kind"
  local logflag=""
  if [ ! -f "study/.trials-logged-$kind" ]; then logflag="--log-trials"; fi
  echo "== $(date -u +%FT%TZ) grid $label start" >> "$LOG"
  THREADS=3 /opt/alpha-compute/bin/run-grid.sh "$label" "$@" >> "$LOG" 2>&1
  sleep 5
  while systemctl is-active --quiet "alpha-grid-$label"; do sleep 30; done
  echo "== $(date -u +%FT%TZ) grid $label done: $(tail -1 b5/$label/grid.err 2>/dev/null | cut -c1-200)" >> "$LOG"
  if $BIN lob bounce-verdict --grid-dir "b5/$label" --runs-csv "$RUNS" --out "study/bounce-verdict-$label.csv" $logflag > "study/bounce-verdict-$label.log" 2>&1; then
    [ -n "$logflag" ] && touch "study/.trials-logged-$kind"
  fi
  echo "== $(date -u +%FT%TZ) verdict $label: $(tail -3 study/bounce-verdict-$label.log | tr '\n' ' ' | cut -c1-300)" >> "$LOG"
}
echo "== $(date -u +%FT%TZ) side chain start; days: $(echo "$DAYS_ALL" | tr '\n' ' '); окно: ${DAYS_WINDOW:-все}; bin: $(readlink $BIN)" >> "$LOG"
for side in bid ask; do
  run_one "a45-$side"  $USD --min-age-secs 2700 --side "$side" $BASE $DAY_ARGS
  run_one "s100-$side" $USD --min-flow-pct 100  --side "$side" $BASE $DAY_ARGS
done
echo "== $(date -u +%FT%TZ) side chain done" >> "$LOG"
