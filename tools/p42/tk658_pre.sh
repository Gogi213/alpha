#!/usr/bin/env bash
# П-42 §13.58 (TK-658): движок (базы с P42_DETAIL=1 + slip 0/2/3/5), G-base, замер ленты, блок V — без P&L. Только после PASS критика на код.
# Запуск: systemd-run --unit=tk658-pre --collect -q -p Nice=19 -p CPUQuota=600% -p CPUWeight=1 -p IOSchedulingClass=idle bash /data/p42-tk658/code/tk658_pre.sh ; код — /data/p42-tk658/rc/pre.
set -uo pipefail
ROOT=/data/p42-tk658; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"
export PYTHONUTF8=1
{ /opt/venv/bin/python tk658_run.py eng && /opt/venv/bin/python tk658_run.py tape --procs 6 && /opt/venv/bin/python tk658_run.py cal13; } > "$ROOT/log/pre.log" 2>&1
echo $? > "$ROOT/rc/pre"
