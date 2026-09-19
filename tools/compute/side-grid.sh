#!/usr/bin/env bash
# Ось стороны (этап 1 docs/plan/alpha-roadmap-2026-09-19.md; намёк — side-asymmetry-2026-09-19.md):
# семьи флоров a45 (возраст ≥ 2700 с) и s100 (сила ×поток ≥ 100 %) × сторона bid|ask × 32 формы
# базы В-65 = 128 испытаний (предрегистрация — строка prereg в runs.csv ДО запуска). Сетки идут
# последовательно на всех сутках корня (или окне DAYS_WINDOW, как у nightly-grid.sh), после каждой —
# вердикт; испытания регистрируются в журнале один раз на вид сетки (маркер study/.trials-logged-<kind>),
# повторные прогоны по новым суткам журнал не раздувают. Запуск на счётной машине — семья юнитом,
# две параллельно (по ядру на реплей книги), монеты — из сетки `-any` той же семьи:
#   for f in a45:v70bal-a45-any s100:v68lat-base-any; do fam=${f%%:*}; any=${f#*:}; \
#     systemd-run --unit alpha-chain-side-$fam -p WorkingDirectory=/opt/alpha-compute \
#       -E FAMILIES=$fam -E LOG_SUFFIX=-$fam -E PARALLEL_OK=1 -E SYMBOLS_FROM=b5/$any/forms.csv \
#       -p StandardOutput=append:/opt/alpha-compute/study/chain-side-$fam.log \
#       -p StandardError=append:/opt/alpha-compute/study/chain-side-$fam.log /opt/alpha-compute/bin/side-grid.sh; done
# Артефакты: b5/side-<день>/<семья>-<сторона>/ (один процесс, --set), study/bounce-verdict-side-<день>-*.csv,
# лог study/chain-side.log; срез — side-axis.py --prefix b5/side-<день>/ (с косой чертой в конце).
# Ускорение (19.09): реплей книги — одно ядро на монету и ≈ 50–130 с, формы дешевле; сторона —
# подмножество семьи, поэтому монеты без сигналов в сетке `-any` не гоняем (SYMBOLS_FROM=<forms.csv
# семьи -any>: список монет с n_signals > 0), а семьи идут параллельно (FAMILIES="a45" и "s100" двумя
# юнитами, LOG_SUFFIX=-<семья>, PARALLEL_OK=1 снимает проверку «сетка уже идёт» — она защищает
# только ночной таймер); SIDES="bid" — одна сторона на юнит (четыре юнита по ядру, THREADS=1).
set -uo pipefail
cd /opt/alpha-compute || exit 1
DAY=$(date -u +%F)
FAMILIES="${FAMILIES:-a45 s100}"
SIDES="${SIDES:-bid ask}"
THREADS="${THREADS:-3}"
LOG=study/chain-side${LOG_SUFFIX:-}.log
BIN=/opt/alpha-compute/bin/alpha
RUNS=study/runs-2026-09-19.csv
DAYS_WINDOW="${DAYS_WINDOW:-}"
DAYS_ALL=$(ls root/*.binlog* 2>/dev/null | sed -E 's/.*-([0-9]{4}-[0-9]{2}-[0-9]{2}).*/\1/' | sort -u)
DAY_ARGS=""
if [ -n "$DAYS_WINDOW" ]; then
  for d in $(echo "$DAYS_ALL" | tail -n "$DAYS_WINDOW"); do DAY_ARGS="$DAY_ARGS --day $d"; done
fi
if [ -z "${PARALLEL_OK:-}" ] && systemctl list-units "alpha-grid-*" --no-legend | grep -q running; then
  echo "== $(date -u +%FT%TZ) сетка ещё идёт — ось стороны не запущена" >> "$LOG"; exit 0
fi
# Монеты семьи: из forms.csv сетки `-any` (n_signals > 0 хоть у одной формы); без переменной — весь пул.
symbols_of() {
  [ -n "${1:-}" ] || return 0
  python3 - "$1" <<'PY'
import csv, sys
rows = [r for r in csv.DictReader(l for l in open(sys.argv[1], encoding="utf-8") if not l.startswith("#"))]
print(" ".join(f"--symbol {s}" for s in sorted({r["symbol"] for r in rows if int(r["n_signals"] or 0) > 0})))
PY
}
# Касания — из кэша ночного H3 (20.09, гейт пройден — COMMANDS.md); без суток в кэше монета реплеится сама.
USD="--h3-mode notional --h3-usd 10000 --touches-from study/touches"
BASE="--stop-form before --stop-form at --stop-form behind --stop-form midfr --stop-form stack2 --stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form 1to1"
run_one() {
  local kind=$1; shift
  local label="side-$DAY-$kind"
  local logflag=""
  if [ ! -f "study/.trials-logged-$kind" ]; then logflag="--log-trials"; fi
  echo "== $(date -u +%FT%TZ) grid $label start" >> "$LOG"
  THREADS=$THREADS /opt/alpha-compute/bin/run-grid.sh "$label" "$@" >> "$LOG" 2>&1
  sleep 5
  while systemctl is-active --quiet "alpha-grid-$label"; do sleep 30; done
  echo "== $(date -u +%FT%TZ) grid $label done: $(tail -1 b5/$label/grid.err 2>/dev/null | cut -c1-200)" >> "$LOG"
  if $BIN lob bounce-verdict --grid-dir "b5/$label" --runs-csv "$RUNS" --out "study/bounce-verdict-$label.csv" $logflag > "study/bounce-verdict-$label.log" 2>&1; then
    [ -n "$logflag" ] && touch "study/.trials-logged-$kind"
  fi
  echo "== $(date -u +%FT%TZ) verdict $label: $(tail -3 study/bounce-verdict-$label.log | tr '\n' ' ' | cut -c1-300)" >> "$LOG"
}
echo "== $(date -u +%FT%TZ) side chain start; families: $FAMILIES; days: $(echo "$DAYS_ALL" | tr '\n' ' '); окно: ${DAYS_WINDOW:-все}; symbols-from: ${SYMBOLS_FROM:-весь пул}; bin: $(readlink $BIN)" >> "$LOG"
SYMS=$(symbols_of "${SYMBOLS_FROM:-}")
[ -n "$SYMS" ] && echo "== монет в семье: $(echo "$SYMS" | wc -w | awk '{print $1/2}')" >> "$LOG"
# Один процесс на все семьи × стороны (`--set`, 20.09): события и окна суток один раз; вердикт на набор.
SETS=""; KINDS=""
for fam in $FAMILIES; do
  case "$fam" in
    a45)  FLOOR="age=2700" ;;
    s100) FLOOR="flow=100" ;;
    *) echo "== неизвестная семья $fam" >> "$LOG"; continue ;;
  esac
  for side in $SIDES; do SETS="$SETS --set $fam-$side:$FLOOR,side=$side"; KINDS="$KINDS $fam-$side"; done
done
label="side-$DAY"
echo "== $(date -u +%FT%TZ) grid $label start:$SETS" >> "$LOG"
THREADS=$THREADS /opt/alpha-compute/bin/run-grid.sh "$label" $USD $BASE $DAY_ARGS $SYMS $SETS >> "$LOG" 2>&1
sleep 5
while systemctl is-active --quiet "alpha-grid-$label"; do sleep 30; done
echo "== $(date -u +%FT%TZ) grid $label done: $(tail -1 b5/$label/grid.err 2>/dev/null | cut -c1-200)" >> "$LOG"
for kind in $KINDS; do
  logflag=""
  if [ ! -f "study/.trials-logged-$kind" ]; then logflag="--log-trials"; fi
  if $BIN lob bounce-verdict --grid-dir "b5/$label/$kind" --runs-csv "$RUNS" --out "study/bounce-verdict-$label-$kind.csv" $logflag > "study/bounce-verdict-$label-$kind.log" 2>&1; then
    [ -n "$logflag" ] && touch "study/.trials-logged-$kind"
  fi
  echo "== $(date -u +%FT%TZ) verdict $label-$kind: $(tail -3 study/bounce-verdict-$label-$kind.log | tr '\n' ' ' | cut -c1-300)" >> "$LOG"
done
echo "== $(date -u +%FT%TZ) side chain done" >> "$LOG"
