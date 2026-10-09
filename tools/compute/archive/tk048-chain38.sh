#!/bin/bash
# chain38: холодная волна q15 wide b14, PF=5 точного набора + прогрев .abin подходов по суткам (WARM_ABIN) без CSV (WARM_NOCSV). База q15b14x5 239,7 с.
rm -f /data/tk048/chain38.done
D15=$(seq -s, -f "2026-01-%02g" 1 15)
systemd-run --wait --collect --unit tk048-q15b14y -p CPUQuota=1500% --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp14.sh q15b14y alpha-b14flag $D15 8 15 > /dev/null 2>&1
touch /data/tk048/chain38.done
