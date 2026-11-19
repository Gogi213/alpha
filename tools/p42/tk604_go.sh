#!/usr/bin/env bash
# П-42 §13.26 (TK-604): запуск шага драйвера на srv юнитом systemd (≤ 4 процессов: Nice=19, CPUQuota=400%, idle IO). Код — /data/p42-tk604/code.
#   bash tk604_go.sh <имя> <подкоманда tk604_run.py> [аргументы…]   → юнит tk604-<имя>, код возврата — файл /data/p42-tk604/rc/<имя>
# Ожидание: `bash tools/srv-wait.sh file:/data/p42-tk604/rc/<имя>` (фоном, timeout 7200000).
set -uo pipefail
NAME="${1:?имя}"; shift
ROOT=/data/p42-tk604
mkdir -p "$ROOT/rc" "$ROOT/log"; rm -f "$ROOT/rc/$NAME"
systemd-run --unit="tk604-$NAME" --collect -q -p WorkingDirectory="$ROOT/code" -p Nice=19 -p CPUQuota=400% -p IOSchedulingClass=idle \
  bash -c 'python3 tk604_run.py "$@" > "'"$ROOT"'/log/'"$NAME"'.log" 2>&1; echo $? > "'"$ROOT"'/rc/'"$NAME"'"' _ "$@"
