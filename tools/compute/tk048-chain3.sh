#!/bin/bash
# tk048-chain3.sh: Г5 — читатель по готовности D (READY_D=1: сайдкары + файлы суток d, D+1 дочитывается позже), QUICK5 против опорных grpq5/r2q5n1;
# затем «связка целиком»: 15 суток 01–15.01, G=8, READY_D=1, alpha-b4-pgo + ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1. Перед каждой волной ждёт отсутствия чужих flock-юнитов.
Q=$(cat /data/tk048/QUICK5); T=/data/tk048/tk048-orch
D15=$(seq -s, -f '2026-01-%02g' 1 15)
run() { systemd-run --wait --collect --unit tk048-$3 -p CPUQuota=1500% --setenv=READERS=1 --setenv=READY_D=1 "${ENVX[@]}" /data/benchrun.sh wave "$@" > /dev/null 2>&1; }
ENVX=()
run bash $T-r1.sh r2q5rd alpha-tk048rd $Q 8 15
ENVX=(--setenv=ALPHA_SKIP_SAME=1 --setenv=ALPHA_EVENT_STEPS=1)
run bash $T-r1.sh bundle15 alpha-b4-pgo $D15 8 15
