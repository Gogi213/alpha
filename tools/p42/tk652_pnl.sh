#!/usr/bin/env bash
# П-42 §13.52 (TK-652): cells → tables → verdict → report одним юнитом (P&L — только после PASS критика).
# Запуск: systemd-run --unit=tk652-pnl --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk652/code/tk652_pnl.sh ; код — /data/p42-tk652/rc/pnl.
set -uo pipefail
ROOT=/data/p42-tk652; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/pnl"
LOG=$ROOT/log/run.log
step() { local name=$1; [ -e "$ROOT/rc/step-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 TK643_ROOT=$ROOT python3 tk652_run.py "$name" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/pnl"; exit 1; }; touch "$ROOT/rc/step-$name"; }
for s in variants cells tables verdict report; do step $s; done
echo 0 > "$ROOT/rc/pnl"
