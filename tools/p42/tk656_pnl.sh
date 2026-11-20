#!/usr/bin/env bash
# П-42 §13.56 (TK-656): таблицы P&L — только после PASS критика, один раз.
# Запуск: systemd-run --unit=tk656-pnl --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk656/code/tk656_pnl.sh ; код — /data/p42-tk656/rc/pnl.
set -uo pipefail
ROOT=/data/p42-tk656; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"
[ -e "$ROOT/rc/pnl" ] && { echo "уже считано"; exit 0; }
PYTHONUTF8=1 python3 tk656_post.py tables > "$ROOT/log/tables.log" 2>&1; echo $? > "$ROOT/rc/pnl"
