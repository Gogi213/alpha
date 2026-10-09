#!/bin/bash
# chain36: холодная волна q15 wide b14 с читателем вперёд ТОЧНОГО набора (symbol,сутки) из q15b14n (PFLIST, 438 из 3782) — верхняя оценка выигрыша читателя
rm -f /data/tk048/chain36.done
D15=$(seq -s, -f "2026-01-%02g" 1 15)
systemd-run --wait --collect --unit tk048-q15b14x -p CPUQuota=1500% --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=2 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp13.sh q15b14x alpha-b14flag $D15 8 15 > /dev/null 2>&1
touch /data/tk048/chain36.done
