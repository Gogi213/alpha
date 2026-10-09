#!/bin/bash
# tk048-chain5.sh: перемер в тишине (benchrun wave замораживает чужие юниты): (1) связка целиком БЕЗ читателя: 15 суток, G=8, P=15, alpha-b4-pgo + ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1;
# (2) QUICK5 (rd): опорная (grp), читатель n1, читатель READY_D.
Q=$(cat /data/tk048/QUICK5); T=/data/tk048/tk048-orch
D15=$(seq -s, -f '2026-01-%02g' 1 15)
run() { systemd-run --wait --collect --unit tk048-$3 -p CPUQuota=1500% --setenv=READERS=1 "${ENVX[@]}" /data/benchrun.sh wave "$@" > /dev/null 2>&1; }
ENVX=(--setenv=ALPHA_SKIP_SAME=1 --setenv=ALPHA_EVENT_STEPS=1)
run bash $T-grp.sh q3b15 alpha-b4-pgo $D15 8 15
ENVX=()
run bash $T-grp.sh q3q5ref alpha-tk048rd $Q 8 15
run bash $T-r1.sh q3q5n1 alpha-tk048rd $Q 8 15
ENVX=(--setenv=READY_D=1)
run bash $T-r1.sh q3q5rd alpha-tk048rd $Q 8 15
