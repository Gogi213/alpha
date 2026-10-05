#!/bin/bash
# tk048-chain4.sh: перемер в тишине (приоритетная волна, benchrun.sh): (1) bundle15 — связка целиком; (2) QUICK5: опорная (rd, без READY_D), n1+READY_D, G=4, G=8 (rd+READY_D).
Q=$(cat /data/tk048/QUICK5); T=/data/tk048/tk048-orch
D15=$(seq -s, -f '2026-01-%02g' 1 15)
run() { systemd-run --wait --collect --unit tk048-$3 -p CPUQuota=1500% --setenv=READERS=1 "${ENVX[@]}" /data/benchrun.sh wave "$@" > /dev/null 2>&1; }
ENVX=(--setenv=READY_D=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=ALPHA_EVENT_STEPS=1)
run bash $T-r1.sh q2bundle15 alpha-b4-pgo $D15 8 15
ENVX=()
run bash $T-r1.sh q2q5ref alpha-tk048rd $Q 8 15
ENVX=(--setenv=READY_D=1)
run bash $T-r1.sh q2q5rd8 alpha-tk048rd $Q 8 15
run bash $T-r1.sh q2q5rd4 alpha-tk048rd $Q 4 15
