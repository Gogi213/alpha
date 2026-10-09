#!/bin/bash
# chain31: тёплый повтор q15 wide на b13 без сброса кэша: w1 — после полного прогона (кэш частично), w2 — сразу следом (кэш тёплый)
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for n in w1 w2; do
systemd-run --wait --collect --unit tk048-q15b13$n -p CPUQuota=1500% --setenv=NODROP=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp11.sh q15b13$n alpha-b13flag $D15 8 15 > /dev/null 2>&1
done
touch /data/tk048/chain31.done
