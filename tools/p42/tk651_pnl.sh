#!/usr/bin/env bash
# П-42 §13.51 (TK-651): таблицы --ftab + движок (клетки) + ворота + счёт без P&L; шаг `tables` (P&L) — только после PASS критика (`STAGE=pnl`).
# Запуск: systemd-run --unit=tk651-<этап> --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle -E STAGE=<pre|pnl> bash /data/p42-tk651/code/tools/p42/tk651_pnl.sh ; код — /data/p42-tk651/rc/<этап>.
set -uo pipefail
ROOT=/data/p42-tk651; cd "$ROOT/code/tools/p42"; mkdir -p "$ROOT/log" "$ROOT/rc"; S=${STAGE:?pre|pnl}; rm -f "$ROOT/rc/$S"
LOG=$ROOT/log/run.log
step() { local name=$1; shift; [ -e "$ROOT/rc/step-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 tk651_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/$S"; exit 1; }; touch "$ROOT/rc/step-$name"; }
if [ "$S" = pre ]; then
  step ftab ftab
  step cells cells --threads 2
  step gate gate
  step counts counts
else
  step tables tables
fi
echo 0 > "$ROOT/rc/$S"
