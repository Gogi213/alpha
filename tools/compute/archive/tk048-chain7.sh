#!/bin/bash
# tk048-chain7.sh: после chain6 — загадка ЦП (CEO 06:45): QUICK5, подогрев везде; пара tk048rd без флагов / b4-pgo с флагами (P=15) и развёртка P=8,11 на b4-pgo.
# В metrics.txt каждой волны: cpu_user_s/cpu_sys_s, proof_* (md5, exe, ALPHA_* живого процесса), perf instructions/cycles/IPC.
while systemctl is-active --quiet tk048-chain5.service tk048-chain6.service; do sleep 5; done
Q=$(cat /data/tk048/QUICK5); T=/data/tk048/tk048-orch
run() { systemd-run --wait --collect --unit tk048-$3 -p CPUQuota=1500% "${ENVX[@]}" /data/benchrun.sh wave "$@" > /dev/null 2>&1; }
ENVX=(--setenv=PREWARM_SMALL=1)
run bash $T-grp.sh q4rd alpha-tk048rd $Q 8 15
ENVX=(--setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=ALPHA_EVENT_STEPS=1)
run bash $T-grp.sh q4pgo alpha-b4-pgo $Q 8 15
run bash $T-grp.sh q4p11 alpha-b4-pgo $Q 8 11
run bash $T-grp.sh q4p8 alpha-b4-pgo $Q 8 8
