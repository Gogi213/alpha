#!/usr/bin/env bash
# П-42 §13.49 (TK-649): шаги на srv. `gbyte` — до PASS критика (базы, P&L известен); `pnl` — после PASS: клетки T, таблицы и ворота, вердикт п. 7, G-win.
# Запуск: systemd-run --unit=tk649-<шаг> --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk649/code/tk649_pnl.sh <gbyte|pnl>
# Код — /data/p42-tk649/rc/<шаг> (0 = всё прошло, иначе `шаг:код`).
set -uo pipefail
ROOT=/data/p42-tk649; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"
WHAT=${1:?gbyte|pnl}; rm -f "$ROOT/rc/$WHAT"
LOG=$ROOT/log/$WHAT.log
step() { local name=$1; shift; [ -e "$ROOT/rc/pstep-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 tk649_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/$WHAT"; exit 1; }; touch "$ROOT/rc/pstep-$name"; }
case "$WHAT" in
  gbyte) step gbyte gbyte ;;
  pnl) step cells cells; step tables tables; step verdict verdict; step gwin gwin ;;
  *) echo "gbyte|pnl"; exit 2 ;;
esac
echo 0 > "$ROOT/rc/$WHAT"
