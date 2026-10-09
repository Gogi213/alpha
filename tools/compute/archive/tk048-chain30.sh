#!/bin/bash
# chain30: ПОЛНЫЙ янв+фев на alpha-b13flag + --events wide (b13), grpg: shm-выход; вывод /data/tk048/full-b13(.out)
avail=$(df -BG --output=avail /data | tail -1 | tr -dc 0-9); echo "avail_GB $avail" > /data/tk048/chain30.log
[ "$avail" -gt 150 ] || { echo LOW_DISK >> /data/tk048/chain30.log; exit 1; }
systemd-run --wait --collect --unit tk048-full-b13 -p CPUQuota=1500% --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 /data/tk052/benchrun2.sh wave bash /data/tk048/tk048-orch-grpg.sh full-b13 alpha-b13flag 8 15 > /dev/null 2>&1
touch /data/tk048/chain30.done
