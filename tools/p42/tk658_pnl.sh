#!/usr/bin/env bash
# П-42 §13.58 (TK-658): блоки P и E, G-path, G-splice, правило п. 7 — один раз, после tk658_pre (код 0).
# Запуск: systemd-run --unit=tk658-pnl --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk658/code/tk658_pnl.sh ; код — /data/p42-tk658/rc/pnl.
set -uo pipefail
ROOT=/data/p42-tk658; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"
[ -e "$ROOT/rc/pnl" ] && { echo "уже считано"; exit 0; }
[ "$(cat "$ROOT/rc/pre" 2>/dev/null)" = "0" ] || { echo "нет pre"; exit 3; }
PYTHONUTF8=1 /opt/venv/bin/python tk658_run.py pnl > "$ROOT/log/pnl.log" 2>&1; echo $? > "$ROOT/rc/pnl"
