#!/usr/bin/env bash
# TK-279 (В-21): досборка боевого PGO r2-бинаря П-17 с шага use (gen/plain/train готовы, merged.profdata есть) + гейт pgo=plain.
# Причина повтора: use 1016221953854 заморожен «перегрузом» (guard_load) и убит по RuntimeMaxSec=60m стены (extend — только при разморозке) → max-runtime 300m.
set -u
TAG=tk279; SRC=/data/tk0279/src-pgo; W=/data/tk0279; L=$W/pgo-chain.log; B=/data/tk0231/build-pgo.sh
rm -f $W/pgo.ok $W/pgo.fail
waitrc() { [ -n "$1" ] || return 9; while [ ! -f /data/sched/rc/$1 ]; do sleep 20; done; return "$(cat /data/sched/rc/$1)"; }
fail() { echo "FAIL $1" | tee -a $L; touch $W/pgo.fail; exit 1; }
u=$(python3 /data/sched/alsched.py submit --name tk279-pgo-use2 --kind build --cores 3 --mem 12 --max-runtime 300m --est 30m --adhoc "TK-279 PGO use повтор (после заморозки), В-21" --reason "сборка PGO, не проход счёта" --cwd $W -- env FEATURES=r2 bash $B $TAG $SRC use 2>&1 | tee -a $L | grep -o '[0-9]\{13\}' | head -1)
echo "use2 $u" >> $L; waitrc "$u" || fail use
g=$(python3 /data/sched/alsched.py submit --name tk279-pgo-gate --kind gate --cores 3 --mem 12 --max-runtime 180m --est 40m --adhoc "TK-279 гейт pgo=plain" --reason "гейт, 1 сутки 10 клеток" --cwd $W -- bash $W/gate-pgo.sh 2>&1 | tee -a $L | grep -o '[0-9]\{13\}' | head -1)
echo "gate $g" >> $L; waitrc "$g" || fail gate
