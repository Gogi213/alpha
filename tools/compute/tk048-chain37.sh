#!/bin/bash
# chain37: холодные волны q15 wide b14, читатель вперёд точного набора (PFLIST) глубже: PREFETCH=5 и PREFETCH=15 (всё сразу). База: q15b14n 251,6 с, q15b14x (PF=2) 244,8 с.
rm -f /data/tk048/chain37.done
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for pf in 5 15; do
  systemd-run --wait --collect --unit tk048-q15b14x$pf -p CPUQuota=1500% --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=$pf --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp13.sh q15b14x$pf alpha-b14flag $D15 8 15 > /dev/null 2>&1
done
touch /data/tk048/chain37.done
