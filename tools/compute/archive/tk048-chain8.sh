#!/bin/bash
# tk048-chain8.sh: после chain7 — связка целиком alpha-b6 (TK-051): 15 суток, G=8, P=15, подогрев, флаги.
while systemctl is-active --quiet tk048-chain5.service tk048-chain6.service tk048-chain7.service; do sleep 5; done
T=/data/tk048/tk048-orch; D15=$(seq -s, -f '2026-01-%02g' 1 15)
systemd-run --wait --collect --unit tk048-q4b6 -p CPUQuota=1500% --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=ALPHA_EVENT_STEPS=1 /data/benchrun.sh wave bash $T-grp.sh q4b6 alpha-b6 $D15 8 15 > /dev/null 2>&1
