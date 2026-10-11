#!/bin/bash
# TK-178 Д1: волна января как TK-177 (p177), но read_ahead 512 КБ на sda/sdb; значение возвращается.
mkdir -p /tmp/tk178; cd /tmp/tk178
R0a=$(blockdev --getra /dev/sda); R0b=$(blockdev --getra /dev/sdb)
echo "ra0 sda=$R0a sdb=$R0b" > ra.log
trap 'blockdev --setra $R0a /dev/sda; blockdev --setra $R0b /dev/sdb; echo "ra restored $(blockdev --getra /dev/sda) $(blockdev --getra /dev/sdb)" >> /tmp/tk178/ra.log' EXIT
blockdev --setra 1024 /dev/sda; blockdev --setra 1024 /dev/sdb
echo "ra set $(blockdev --getra /dev/sda) $(blockdev --getra /dev/sdb)" >> ra.log
D=$(seq -s, -f "2026-01-%02g" 1 31)
t0=$(date +%s)
systemd-run --wait --collect --unit tk178-ra-wave -p CPUQuota=1500% \
 --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=EVENTS_WIDE=1 \
 bash /data/tk048/tk048-orch-grp16q.sh p178ra alpha-tk174b-pgo "$D" 8 20 > run.out 2>&1
echo "wall_s $(( $(date +%s)-t0 ))" > summary.txt
touch ra.done
