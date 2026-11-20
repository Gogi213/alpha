#!/usr/bin/env bash
# П-42 §13.56 (TK-656): этап без P&L — признаки, ворота, counts, движок (ALL1 + 12 клеток на сторону), G-base / G8, число сделок клеток. P&L не читает.
# Запуск: systemd-run --unit=tk656-pre --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle bash /data/p42-tk656/code/tk656_pre.sh ; код — /data/p42-tk656/rc/pre (0 или `шаг:код`).
set -uo pipefail
ROOT=/data/p42-tk656; cd "$ROOT/code"; mkdir -p "$ROOT/log" "$ROOT/rc"; rm -f "$ROOT/rc/pre"
LOG=$ROOT/log/pre.log
step() { local name=$1 py=$2; shift 2; [ -e "$ROOT/rc/step-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 python3 "$py" "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/pre"; exit 1; }; touch "$ROOT/rc/step-$name"; }
step feat tk656_run.py feat
step gate tk656_run.py gate
step counts tk656_run.py counts
step cells tk656_post.py cells
step gbase tk656_post.py gate
step ncount tk656_post.py ncount
echo 0 > "$ROOT/rc/pre"
