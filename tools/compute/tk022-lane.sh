#!/usr/bin/env bash
# TK-022: полоса счёта на VPS. tk022-lane.sh <файл со списком "мес сутки" по порядку>
# Сутки забираются атомарно (mkdir в /home/deck/alpha/tk022/claim) — любое число полос делит список само.
# Итог суток — $H/vps-status/<сутки>.rc (rc=0 sec=N); сценарий — `p07-all-month.py` (jall июля, подмена месяца).
set -u
LIST="${1:?список}"
CL=/home/deck/alpha/tk022/claim
LG=/home/deck/alpha/tk022/log
mkdir -p "$CL" "$LG"
while read -r mon day; do
  [ -n "${day:-}" ] || continue
  H=/home/deck/alpha/epochs/e-$mon
  [ -e "$H/vps-status/$day.rc" ] && continue
  mkdir "$CL/$day" 2>/dev/null || continue
  cd "$H" || continue
  s=$(date +%s)
  bash "/home/deck/alpha/tmp-p07/cells-by-day/jall-$mon-$day.sh" > "$LG/$mon-$day.log" 2>&1
  rc=$?
  echo "rc=$rc sec=$(( $(date +%s) - s ))" > "$H/vps-status/$day.rc"
done < "$LIST"
