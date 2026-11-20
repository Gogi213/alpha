#!/usr/bin/env bash
# П-42 §13.54 (TK-654): пакет после PASS критика — клетки h775 (+ контроль CTRL), G8 + G-base, таблицы. Читает P&L: только после PASS.
# Запуск: systemd-run --unit=tk654-pnl --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk654/code/tk654_pnl.sh ; код возврата — /data/p42-tk654/rc/pnl.
set -uo pipefail
ROOT=/data/p42-tk654; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/pnl"
LOG=$ROOT/log/pnl.log
step() { local name=$1; shift; [ -e "$ROOT/rc/pstep-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 tk654_post.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/pnl"; exit 1; }; touch "$ROOT/rc/pstep-$name"; }
step cells cells
step g8 g8
step tables tables
echo 0 > "$ROOT/rc/pnl"
