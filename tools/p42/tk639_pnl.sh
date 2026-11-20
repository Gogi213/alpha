#!/usr/bin/env bash
# П-42 §13.37 (TK-639): пакет после PASS критика — клетки лестниц h775 и o566, G8, таблицы. Читает P&L: только после PASS.
# Запуск: systemd-run --unit=tk639-pnl --collect -q -p Nice=19 -p CPUQuota=400% -p IOSchedulingClass=idle bash /data/p42-tk639/code/tk639_pnl.sh ; код возврата — /data/p42-tk639/rc/pnl (0 = всё прошло, иначе `шаг:код`).
set -uo pipefail
ROOT=/data/p42-tk639; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/pnl"
LOG=$ROOT/log/pnl.log
step() { local name=$1; shift; [ -e "$ROOT/rc/pstep-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 tk639_post.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/pnl"; exit 1; }; touch "$ROOT/rc/pstep-$name"; }
step cells-h cells --uni h775
step cells-o cells --uni o566
step g8-h g8 --uni h775
step g8-o g8 --uni o566
step tables-h tables --uni h775
step tables-o tables --uni o566
echo 0 > "$ROOT/rc/pnl"
