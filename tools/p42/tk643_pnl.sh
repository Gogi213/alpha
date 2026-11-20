#!/usr/bin/env bash
# П-42 §13.43 (TK-643): пакет после PASS критика — блоки A–C (движок), таблицы и ворота, вердикт п. 7, описательный блок D. Читает P&L: только после PASS.
# Запуск: systemd-run --unit=tk643-pnl --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk643/code/tk643_pnl.sh ; код — /data/p42-tk643/rc/pnl (0 = всё прошло, иначе `шаг:код`).
set -uo pipefail
ROOT=/data/p42-tk643; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/pnl"
LOG=$ROOT/log/pnl.log
step() { local name=$1; shift; [ -e "$ROOT/rc/pstep-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 tk643_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/pnl"; exit 1; }; touch "$ROOT/rc/pstep-$name"; }
step variants variants
step cells cells
step tables tables
step verdict verdict
step cross cross
echo 0 > "$ROOT/rc/pnl"
