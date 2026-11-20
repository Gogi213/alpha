#!/usr/bin/env bash
# П-42 §13.53 (TK-653): двойники сигналов → строки → 4 модели на сторону (заморозка sha256) → --ftab → движок → ворота → счёт без P&L (STAGE=fit); таблицы P&L — STAGE=pnl (после коммита заморозки моделей).
# Запуск: systemd-run --unit=tk653-<этап> --collect -q -p Nice=19 -p CPUQuota=200% -p IOSchedulingClass=idle -E STAGE=<fit|pnl> bash /data/p42-tk653/code/tools/p42/tk653_pnl.sh ; код — /data/p42-tk653/rc/<этап>.
set -uo pipefail
ROOT=/data/p42-tk653; cd "$ROOT/code/tools/p42"; mkdir -p "$ROOT/log" "$ROOT/rc"; S=${STAGE:?fit|pnl}; rm -f "$ROOT/rc/$S"
PY=/data/tk594/venv/bin/python   # catboost 1.2.10
LOG=$ROOT/log/run.log
step() { local name=$1; shift; [ -e "$ROOT/rc/step-$name" ] && return 0; echo "$(date -u +%T) $name" >> "$LOG"; PYTHONUTF8=1 "$PY" tk653_run.py "$@" > "$ROOT/log/$name.log" 2>&1; local rc=$?; echo "$(date -u +%T) $name rc=$rc" >> "$LOG"; [ $rc -eq 0 ] || { echo "$name:$rc" > "$ROOT/rc/$S"; exit 1; }; touch "$ROOT/rc/step-$name"; }
if [ "$S" = fit ]; then
  step sig sig --threads 2
  step rows rows
  step fit fit --procs 2
  step ftab ftab
  step cells cells --threads 2
  step gate gate
  step counts counts
else
  date -u +%FT%TZ > "$ROOT/res/READ-pnl.started"
  step tables tables
fi
echo 0 > "$ROOT/rc/$S"
