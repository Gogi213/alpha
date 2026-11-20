#!/usr/bin/env bash
# П-42 §13.25.1 (TK-644): пакет после PASS критика — клетки движка, G8, выбор. Читает P&L: только после PASS.
# Запуск: systemd-run --unit=tk644-pnl --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk644/code/tk644_pnl.sh ; код — /data/p42-tk644/rc/pnl.
set -uo pipefail
ROOT=/data/p42-tk644; PY=/data/tk592/venv/bin/python; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/pnl"
LOG=$ROOT/log/pnl.log
step() { local name=$1; shift; [ -e "$ROOT/rc/pstep-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 $PY tk644_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/pnl"; exit 1; }; touch "$ROOT/rc/pstep-$name"; }
step cells cells
step g8 g8
step select select
echo 0 > "$ROOT/rc/pnl"
