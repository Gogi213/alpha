#!/usr/bin/env bash
# TK-363: боевой прогон движка псевдотеста — pseudotest-run.sh <config.json> <outdir>; итог <outdir>/pseudotest-cells.csv, <outdir>/run.rc.
# Запуск: python3 /data/sched/alsched.py submit --cls prod --kind work --name tk363-<имя> --cores 15 --mem 30 --est 20m --max-runtime 45m -- bash /data/tk0363/pseudotest-run.sh <config> <outdir>
set -uo pipefail
W=/data/tk0363; PY=/data/tk0278/venv/bin/python
mkdir -p "$2"
NX=${NX:-15} $PY $W/pseudotest.py "$1" "$2" > "$2/run.log" 2>&1; echo "$?" > "$2/run.rc"
