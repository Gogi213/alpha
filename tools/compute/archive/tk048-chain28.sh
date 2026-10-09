#!/bin/bash
# chain28: grp11 на 15 суток (b11w, wide): QALT=1 чередование чётных/нечётных суток + допуск по MemAvailable >= 12 ГБ
D15=$(seq -s, -f "2026-01-%02g" 1 15)
systemd-run --wait --collect --unit tk048-q15b11w-alt -p CPUQuota=1500% --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=QALT=1 --setenv=MEMGATE_GB=12 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grp11.sh q15b11walt alpha-b11flag $D15 8 15 > /dev/null 2>&1
touch /data/tk048/chain28.done
