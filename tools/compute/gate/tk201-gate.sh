#!/usr/bin/env bash
# TK-201 (В-227, пакет A): гейт «байт в байт» — сборка головы (боевая и с feature r2) на calc и семьи gate-families.sh
# (diff 0 против опорных семьи на проб-сутках). Запуск ТОЛЬКО через alsched:
#   python3 /data/sched/alsched.py submit --cls prod --name tk201-gate --max-runtime 3h --cores 8 --mem 16 -- bash /data/tk201/tk201-gate.sh
# Исходники: push-head.sh /data/tk201 (снимок головы), calc-build.sh и gate-families.sh рядом (в $W/ или gate/).
# Итог: /data/tk201/gate/summary.txt, маркер gate.done.
set -uo pipefail
W=/data/tk201
B=$(dirname "$(readlink -f "$0")")/calc-build.sh   # TK-207: единая сборка (снимок=голова+vendor, сбой=rc≠0, хеш в бинарнике)
bash "$B" "$W" || exit $?
bash "$B" "$W" r2 || exit $?
md5sum "$W/alpha-new" "$W/alpha-new-r2" > "$W/bin.md5"
GATE_TAG=201 GATE_DIR=$W/gate NEW_R2=$W/alpha-new-r2 bash "$W/gate-families.sh" "$W/alpha-new"
rc=$?; echo "gate rc=$rc"; exit $rc
