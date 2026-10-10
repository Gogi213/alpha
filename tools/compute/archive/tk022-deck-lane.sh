#!/usr/bin/env bash
# TK-022 (В-161): подающий на деке — по датам из ~/alpha/tk022/days.txt ставит сутки в очередь alpha-gridq, как только
# подкачка (.ready суток и, если нужен довесок, D+1) готова. Сам не считает; запуск: systemd-run --user.
#   tk022-deck-lane.sh        claim — mkdir ~/alpha/tk022/claim/<D>; остановка — файл ~/alpha/tk022/stop или gate.FAIL
# Сутки в очереди — задание `bash tk022-deck-day.sh <D>` (3 прохода + ворота «байт в байт» + .release); память — по --mem-gb.
# Конец списка, ожидание .ready > 3 ч — запись в ~/alpha/tk022/wake (по нему будится Исследователь).
set -uo pipefail
A="$HOME/alpha"; T="$A/tk022"
MN=(jan feb mar apr may jun)
MEMGB="${MEMGB:-4.5}"
mkdir -p "$T/claim" "$T/log"
wake() { echo "$(date +%FT%T%z) lane: $*" >> "$T/wake"; }
while read -r D; do
  [ -z "$D" ] && continue
  M="${MN[$((10#${D:5:2} - 1))]}"
  [ -d "$T/claim/$D" ] && continue
  waited=0
  until DRY=1 bash "$A/bin/tk022-deck-day.sh" "$D" >> "$T/log/$D.dry.log" 2>&1; rc=$?; [ $rc = 0 ] || { [ $rc != 4 ] && { wake "сутки $D: вид не собран, rc=$rc (см. $T/log/$D.dry.log)"; exit $rc; }; false; }; do
    [ -e "$T/stop" ] || [ -e "$T/gate.FAIL" ] && exit 0
    sleep 30; waited=$((waited + 30))
    if [ $waited -ge 10800 ]; then wake "сутки $D: .ready не появился за 3 ч"; exit 4; fi
  done
  [ -e "$T/stop" ] || [ -e "$T/gate.FAIL" ] && exit 0
  mkdir "$T/claim/$D" 2>/dev/null || continue
  bash "$A/bin/q-add.sh" --tag tk022 --prio 5 --mem-gb "$MEMGB" --home "$A/epochs/e-$M" --log "$T/log/$D.log" -- bash "$A/bin/tk022-deck-day.sh" "$D" >> "$T/log/lane.log" 2>&1
done < "$T/days.txt"
wake "подающий: список суток кончился"
