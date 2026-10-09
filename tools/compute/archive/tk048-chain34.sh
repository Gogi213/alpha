#!/bin/bash
# chain34: после d20prep — холодная волна q15 wide b13 с читателем вперёд только нужных (символ,сутки), по одному cat на диск (grp12, PREFETCH=2); сравнение с q15b13s2 262,5 с
while [ ! -e /data/tk048/d20prep.done ]; do sleep 30; done
D15=$(seq -s, -f "2026-01-%02g" 1 15)
systemd-run --wait --collect --unit tk048-q15b13p -p CPUQuota=1500% --setenv=PREFETCH=2 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp12.sh q15b13p alpha-b13flag $D15 8 15 > /dev/null 2>&1
touch /data/tk048/chain34.done
