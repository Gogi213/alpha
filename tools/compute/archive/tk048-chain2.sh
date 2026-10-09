#!/bin/bash
# tk048-chain2.sh: Г4 — размер единицы G=16 и G=4 при одном читателе на диск, QUICK5; волны по очереди, перед каждой ждёт отсутствия чужих flock-юнитов.
Q=$(cat /data/tk048/QUICK5); T=/data/tk048/tk048-orch
run() { systemd-run --wait --collect --unit tk048-$3 -p CPUQuota=1500% --setenv=READERS=1 /data/benchrun.sh wave "$@" > /dev/null 2>&1; }
run bash $T-r1.sh r2q5g16 alpha-tk048rd $Q 16 15
run bash $T-r1.sh r2q5g4 alpha-tk048rd $Q 4 15
