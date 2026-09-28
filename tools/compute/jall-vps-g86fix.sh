#!/usr/bin/env bash
# TK-018: сутки VPS, упавшие на проходе Г-86 (лишний --sigma-from, всё прочее посчитано) — дочитать только Г-86.
H=/home/deck/alpha/epochs/e-jul
CD=/home/deck/alpha/tmp-p07/cells-by-day
cd "$H" || exit 1
while :; do
  left=0
  for n in $(seq 22 31); do
    d=2026-07-$n; rc=$H/vps-status/$d.rc
    [ -e "$rc" ] || { left=$((left+1)); continue; }
    grep -q '^rc=0' "$rc" && continue
    [ -e "$rc.g86" ] && continue
    mv "$rc" "$rc.first"
    s=$(date +%s); bash "$CD/vg86-jul-$d.sh" > "$CD/vps-g86-$d.log" 2>&1
    r=$?; touch "$rc.g86"; echo "rc=$r sec=$(( $(date +%s) - s )) g86" > "$rc"
  done
  [ $left = 0 ] && ! ls $H/vps-status/*.rc 2>/dev/null | xargs grep -L '^rc=0' | grep -q . && break
  sleep 60
done
