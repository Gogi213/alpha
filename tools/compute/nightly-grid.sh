#!/usr/bin/env bash
# Ночная сетка на счётной машине (владелец 2026-09-19: «со временем появится ещё день —
# как появится, так потестишь»): после ночного переноса суток (alpha-sync 00:45 UTC на
# коллекторе) гоняет базу В-65 на полах владельца (В-66/67, пол возраста В-70 = 15 мин),
# E7 (В-69), затем вердикты — по ВСЕМ суткам корня (без --day). Испытания каждого вида сетки
# регистрируются в журнале один раз (первая ночь, маркер study/.trials-logged-<вид>).
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
# В-71 (владелец 19.09, вечер): 15 минут с постановки — жёсткий флор (моложе не торгуем);
# возраст выше флора — ось (пул / пулы / по монете — H2 в handoff-2026-09-19.md); сила ×поток ≥ 100 %
# под флором возраста пуста (floors-balance-2026-09-19.md) — сила 10 % и «любая». Контроль —
# прежняя база при силе 100 % без возраста (непрерывность с v66flow/v68lat).
USD="--h3-mode notional --h3-usd 10000"
BASE="--stop-form before --stop-form at --stop-form behind --stop-form midfr --stop-form stack2 --stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form 1to1"
E7="--stop-form pct0.5 --stop-form pct1 --stop-form pct2 --take-form half1to1 --take-form eat50x80 --order-qty-mult 2"
# Испытания регистрируются в журнале один раз на вид сетки (первая ночь) — дальше формы те же.
run_one() {
  local kind=$1; shift
  local label="nightly-$DAY-$kind"
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
  echo "== $(date -u +%FT%TZ) verdict $label: $(tail -3 study/bounce-verdict-$label.log | tr '
' ' ' | cut -c1-300)" >> "$LOG"
}
echo "== $(date -u +%FT%TZ) nightly start; days in root: $(ls root/*.binlog | sed -E 's/.*-(2026-[0-9]{2}-[0-9]{2}).*//' | sort -u | tr '
' ' ')" >> "$LOG"
run_one a15-s10-any   $USD --min-age-secs 900  --min-flow-pct 10 $BASE
run_one a30-any       $USD --min-age-secs 1800 $BASE
run_one a45-any       $USD --min-age-secs 2700 $BASE
run_one a60-any       $USD --min-age-secs 3600 $BASE
run_one s100-any      $USD --min-flow-pct 100 $BASE
run_one e7-a15-s10-any $USD --min-age-secs 900 --min-flow-pct 10 $E7
echo "== $(date -u +%FT%TZ) nightly done" >> "$LOG"
