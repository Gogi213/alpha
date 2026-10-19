#!/usr/bin/env bash
# TK-316: только фьючерс-часть гейта (gate-families base: новый /data/tk316/alpha-new против опорного b14flag). Спот-часть — tk316-gate.sh.
# Скрипты лежат в /tmp/tk316-gate (хук пишет на calc только в разрешённые корни; /data/tk316 к ним не относится).
#   python3 /data/sched/alsched.py submit --kind build --cores 1 --mem 12 --cwd /root --adhoc tk316-gate-fut --reason "гейт TK-316 фьючерс" --max-runtime 3h --name tk316-gate-fut -- bash /tmp/tk316-gate/tk316-gate-fut.sh
set -uo pipefail
W=/data/tk316; NEW=$W/alpha-new; D=/tmp/tk316-gate
GATE_TAG=316 bash "$D/gate-families.sh" "$NEW" base > "$D/gate-fam.log" 2>&1; rc=$?
echo "futures gate-families base rc=$rc: $(cat /data/tk164/gate-316/summary.txt 2>/dev/null | tr '\n' ' ')" | tee -a "$D/summary.txt"
touch "$D/gate-fut.done"
exit $rc
