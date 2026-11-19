#!/usr/bin/env bash
# П-42 §13.33 (TK-633): пакет после PASS критика — ступень 1, клетки h775 и o566, G8, выбор по п. 5, ответ по п. 6. Читает P&L: НЕ запускать до PASS.
# Запуск: systemd-run --unit=tk633-pnl --collect -q -p Nice=19 -p CPUQuota=400% -p IOSchedulingClass=idle bash /data/p42-tk633/code/tk633_pnl.sh ; код возврата — /data/p42-tk633/rc/pnl (0 = всё прошло, иначе `шаг:код`).
set -uo pipefail
ROOT=/data/p42-tk633; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/pnl"
LOG=$ROOT/log/pnl.log
step() { local name=$1; shift; [ -e "$ROOT/rc/pstep-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 tk633_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/pnl"; exit 1; }; touch "$ROOT/rc/pstep-$name"; }
step step1-h step1 --uni h775
step step1-o step1 --uni o566
step cells-h cellsall --uni h775
step cells-o cellsall --uni o566
step g8-h g8 --uni h775
step g8-o g8 --uni o566
step select select
echo 0 > "$ROOT/rc/pnl"
