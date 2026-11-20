#!/usr/bin/env bash
# П-42 §13.54 (TK-654): счёт без P&L на srv — G-u0, эпизоды U0 новых монет и контрольных общих, G-ep, тесты, counts (пороги, клетки, `--ftab`). P&L не читает.
# Запуск: systemd-run --unit=tk654-ep --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk654/code/tk654_chain.sh ; код возврата — /data/p42-tk654/rc/chain.
set -uo pipefail
ROOT=/data/p42-tk654; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/chain"
LOG=$ROOT/log/chain.log
step() { local name=$1; shift; [ -e "$ROOT/rc/step-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/chain"; exit 1; }; touch "$ROOT/rc/step-$name"; }
step gu0 python3 tk654_run.py gu0
step ep-gate python3 tk654_run.py ep --gate --procs 2
step gep python3 tk654_run.py gep
step ep-new python3 tk654_run.py ep --procs 2
step counts python3 tk654_run.py counts
echo 0 > "$ROOT/rc/chain"
