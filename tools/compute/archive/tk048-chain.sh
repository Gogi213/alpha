#!/bin/bash
# tk048-chain.sh: волны TK-048 по очереди; перед каждой ждёт, пока в очереди замка нет чужих юнитов (правило CEO 03:18).
Q=$(cat /data/tk048/QUICK5); T=/data/tk048/tk048-orch
run() { systemd-run --wait --collect --unit tk048-$3 -p CPUQuota=1500% --setenv=READERS=${READERS:-1} /data/benchrun.sh wave "$@" > /dev/null 2>&1; }
run bash $T-grp.sh grpq5ref alpha-tk044k1-new $Q 8 15
READERS=1 run bash $T-r1.sh r2q5n1 alpha-tk048rd $Q 8 15
READERS=2 run bash $T-r1.sh r2q5n2 alpha-tk048rd $Q 8 15
