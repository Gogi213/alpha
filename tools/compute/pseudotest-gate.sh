#!/usr/bin/env bash
# TK-363: гейт движка псевдотеста — две гипотезы (pseudotest-gate.json) = g015-cells.csv (TK-355) до округления; итог /data/tk0363/gate-summary.txt.
# Запуск: python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk363-gate --max-runtime 30m --cores 15 --mem 40 -- bash /data/tk0363/pseudotest-gate.sh
set -uo pipefail
W=/data/tk0363; PY=/data/tk0278/venv/bin/python; S=$W/gate-summary.txt
mkdir -p "$W/out-gate"
s=$(date +%s); NX=15 $PY $W/pseudotest.py $W/pseudotest-gate.json $W/out-gate > $W/gate.log 2>&1; echo "engine rc=$? сек=$(( $(date +%s) - s ))" > "$S"
cat $W/out-gate/pseudotest-summary.json >> "$S" 2>/dev/null
$PY $W/pseudotest-gate.py $W/out-gate/pseudotest-cells.csv $W/g015-cells.csv >> "$S" 2>&1; echo "gate rc=$?" >> "$S"
touch $W/gate.done
