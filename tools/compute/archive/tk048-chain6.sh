#!/bin/bash
# tk048-chain6.sh: после chain5 — связка 15 суток и QUICK5 с подогревом малых файлов (PREWARM_SMALL=1); пары к q3b15 и q3q5ref.
while systemctl is-active --quiet tk048-chain5.service; do sleep 5; done
Q=$(cat /data/tk048/QUICK5); T=/data/tk048/tk048-orch
D15=$(seq -s, -f '2026-01-%02g' 1 15)
run() { systemd-run --wait --collect --unit tk048-$3 -p CPUQuota=1500% "${ENVX[@]}" /data/benchrun.sh wave "$@" > /dev/null 2>&1; }
ENVX=(--setenv=ALPHA_SKIP_SAME=1 --setenv=ALPHA_EVENT_STEPS=1 --setenv=PREWARM_SMALL=1)
run bash $T-grp.sh q3b15w alpha-b4-pgo $D15 8 15
ENVX=(--setenv=PREWARM_SMALL=1)
run bash $T-grp.sh q3q5w alpha-tk048rd $Q 8 15
