#!/usr/bin/env bash
# TK-022 (В-161): по готовности суток (stage/<D>/.ready) ставит в очередь alpha-gridq три прохода суток с локальной копии.
#   tk022-week-submit.sh <YYYY-MM-DD>...   сутки по порядку; запуск на Steam Deck: systemd-run --user (не nohup)
# Проход 1 (основной) ставится сразу; проходы 2 (t9) и 3 (Г-86) — когда проход 1 закончил счёт событий и сетку
# (маркер tk022/status/<D>.p1.grid-done): иначе они одновременно писали бы те же сайдкары .binlog.events.
# Один процесс очереди = один проход (В-136: сетка одним --cells на сутки). Журнал: ~/alpha/tk022/week-submit.log.
set -uo pipefail
A="$HOME/alpha"
MEM1="${MEM1:-4}"; MEM2="${MEM2:-3}"; MEM3="${MEM3:-3}"
MN=(jan feb mar apr may jun)
mkdir -p "$A/tk022/status" "$A/tk022/logs"
LOG="$A/tk022/week-submit.log"
say() { echo "$(date +%T) $*" | tee -a "$LOG"; }

day() {
  local D="$1" M="${MN[$((10#${1:5:2} - 1))]}" N
  N="$(date -u -d "$D +1 day" +%F)"
  local EH="$A/epochs/e-$M"
  [ -e "$A/tk022/status/$D.rc" ] && { say "$D: уже посчитан — пропуск"; return 0; }
  until [ -e "$A/stage/$D/.ready" ] && { ! compgen -G "$EH/root/*-$N.binlog" >/dev/null || [ -e "$A/stage/$N/.ready" ]; }; do sleep 15; done
  say "$D: .ready — собираю вид"
  PREP=1 bash "$A/bin/tk022-deck-day.sh" "$D" >>"$LOG" 2>&1 || { say "$D: PREP упал"; return 1; }
  bash "$A/bin/q-add.sh" --tag tk022-wk --prio 4 --mem-gb "$MEM1" --home "$EH" --log "$A/tk022/logs/$D.p1.log" -- env PASS=1 bash "$A/bin/tk022-deck-day.sh" "$D" >>"$LOG" 2>&1
  say "$D: проход 1 в очереди"
  until [ -e "$A/tk022/status/$D.p1.grid-done" ] || grep -q '^rc=[1-9]' "$A/tk022/status/$D.p1.rc" 2>/dev/null; do sleep 10; done
  grep -q '^rc=[1-9]' "$A/tk022/status/$D.p1.rc" 2>/dev/null && { say "$D: проход 1 упал — 2 и 3 не ставлю"; return 1; }
  bash "$A/bin/q-add.sh" --tag tk022-wk --prio 4 --mem-gb "$MEM2" --home "$EH" --log "$A/tk022/logs/$D.p2.log" -- env PASS=2 bash "$A/bin/tk022-deck-day.sh" "$D" >>"$LOG" 2>&1
  bash "$A/bin/q-add.sh" --tag tk022-wk --prio 4 --mem-gb "$MEM3" --home "$EH" --log "$A/tk022/logs/$D.p3.log" -- env PASS=3 bash "$A/bin/tk022-deck-day.sh" "$D" >>"$LOG" 2>&1
  say "$D: проходы 2, 3 в очереди"
}

for D in "$@"; do day "$D" & sleep 1; done
wait
say "все сутки поставлены"
