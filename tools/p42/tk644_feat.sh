#!/usr/bin/env bash
# П-42 §13.25.1 (TK-644): DERIV14 + TOX13 на кандидатах баз h775 (6 сборок) и counts без P&L.
# Запуск: systemd-run --unit=tk644-feat --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk644/code/tk644_feat.sh ; код — /data/p42-tk644/rc/feat.
set -uo pipefail
ROOT=/data/p42-tk644; PY=/data/tk592/venv/bin/python; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/feat"
LOG=$ROOT/log/feat-chain.log
step() { local name=$1; shift; [ -e "$ROOT/rc/step-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 $PY tk644_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/feat"; exit 1; }; touch "$ROOT/rc/step-$name"; }
for w in wa wb wc; do for s in L S; do step feat-$w-$s feat --window $w --side $s --procs 2; done; done
step counts counts
echo 0 > "$ROOT/rc/feat"
