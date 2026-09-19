#!/usr/bin/env bash
# Ночная сетка на счётной машине (владелец 2026-09-19: «со временем появится ещё день —
# как появится, так потестишь»): после ночного переноса суток (alpha-sync 00:45 UTC на
# коллекторе) гоняет базу В-65 на полах владельца (В-66/67, пол возраста В-70 = 15 мин),
# E7 (В-69), затем вердикты — по ВСЕМ суткам корня (без --day). Те же формы, что в
# предрегистрации, — новых испытаний ночной прогон не добавляет (--log-trials не ставится).
#
#   /opt/alpha-compute/bin/nightly-grid.sh            # из alpha-grid-nightly.timer (02:00 UTC)
# Артефакты: b5/nightly-<день>-<метка>/, вердикты study/bounce-verdict-nightly-<день>-<метка>.csv,
# лог study/nightly-<день>.log. Если сетка предыдущей ночи ещё идёт — выход без запуска.
set -uo pipefail
cd /opt/alpha-compute || exit 1
export PATH=/root/.cargo/bin:$PATH
DAY=$(date -u +%F)
LOG=study/nightly-$DAY.log
BIN=/opt/alpha-compute/bin/alpha
RUNS=study/runs-2026-09-19.csv
if systemctl list-units "alpha-grid-*" --no-legend | grep -q running; then
  echo "== $(date -u +%FT%TZ) сетка ещё идёт — ночной прогон пропущен" >> "$LOG"; exit 0
fi
# Флоры В-66/67 (сила ×поток ≥ 100 %) и В-70 (возраст ≥ 15 мин с постановки) вместе дают ~3 касания
# в сутки на пул (матрица floors-balance.py, 19.09) — поэтому две семьи отдельно: сильные стены
# (сила ≥ 100 %, возраст любой) и старые стены (возраст ≥ 45 мин — с этого порога ход растёт, сила любая).
H3="--h3-mode notional --h3-usd 10000 --min-flow-pct 100"
H3AGE="--h3-mode notional --h3-usd 10000 --min-age-secs 2700"
BASE="--stop-form before --stop-form at --stop-form behind --stop-form midfr --stop-form stack2 --stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form 1to1"
E7="--stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form half1to1 --take-form eat50x80 --order-qty-mult 2"
run_one() {
  local label=$1; shift
  echo "== $(date -u +%FT%TZ) grid $label start" >> "$LOG"
  THREADS=3 /opt/alpha-compute/bin/run-grid.sh "$label" "$@" >> "$LOG" 2>&1
  sleep 5
  while systemctl is-active --quiet "alpha-grid-$label"; do sleep 30; done
  echo "== $(date -u +%FT%TZ) grid $label done: $(tail -1 b5/$label/grid.err 2>/dev/null | cut -c1-200)" >> "$LOG"
  $BIN lob bounce-verdict --grid-dir "b5/$label" --runs-csv "$RUNS" --out "study/bounce-verdict-$label.csv" > "study/bounce-verdict-$label.log" 2>&1
  echo "== $(date -u +%FT%TZ) verdict $label: $(tail -3 study/bounce-verdict-$label.log | tr '\n' ' ' | cut -c1-300)" >> "$LOG"
}
echo "== $(date -u +%FT%TZ) nightly start; days in root: $(ls root/*.binlog | sed -E 's/.*-(2026-[0-9]{2}-[0-9]{2}).*/\1/' | sort -u | tr '\n' ' ')" >> "$LOG"
run_one "nightly-$DAY-base-any" $H3 $BASE
run_one "nightly-$DAY-base-frontrun" $H3 $BASE --frontrun-only
run_one "nightly-$DAY-age45-any" $H3AGE $BASE
run_one "nightly-$DAY-e7-any" $H3 $E7
echo "== $(date -u +%FT%TZ) nightly done" >> "$LOG"
