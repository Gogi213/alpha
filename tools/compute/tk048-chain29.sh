#!/bin/bash
# chain29: волна q15 wide на b13 (alpha-b13flag: флаги b12 + ADMIT_CACHE + HOLDS_MEMO, .abin) против b11w (grp11, без QALT/MEMGATE); гейт diff встроен
D15=$(seq -s, -f "2026-01-%02g" 1 15)
systemd-run --wait --collect --unit tk048-q15b13w -p CPUQuota=1500% --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp11.sh q15b13w alpha-b13flag $D15 8 15 > /dev/null 2>&1
touch /data/tk048/chain29.done
