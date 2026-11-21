#!/usr/bin/env bash
# П-42 §13.69 (TK-669): пакет. Режимы: `pre` — variants, off (строки баз + G-off), counts (счёт без P&L: E57 сделки, E59 события; P&L не читается);
# `pnl` — cells, tables, verdict (читает P&L: только после PASS критика на счёт). Код возврата — /data/p42-tk669/rc/<режим> (0 = всё прошло, иначе `шаг:код`).
# Запуск: systemd-run --unit=tk669-<режим> --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk669/code/tk669_pnl.sh pre|pnl
set -uo pipefail
MODE=${1:?pre|pnl}
ROOT=/data/p42-tk669; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/$MODE"
LOG=$ROOT/log/$MODE.log
step() { local name=$1; shift; [ -e "$ROOT/rc/pstep-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 tk669_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/$MODE"; exit 1; }; touch "$ROOT/rc/pstep-$name"; }
case "$MODE" in
  pre) step variants variants; step off off; step counts counts ;;
  pnl) step cells cells; step tables tables; step verdict verdict ;;
  *) echo "pre|pnl"; exit 2 ;;
esac
echo 0 > "$ROOT/rc/$MODE"
