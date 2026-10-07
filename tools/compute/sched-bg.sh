#!/bin/bash
# TK-071: фон чужих операций диска — холостое окно 5 мин; запуск как замер: systemd-run --unit tk071-bg --collect /data/benchrun.sh wave bash /data/sched/sched-bg.sh
export SCHED_DIR=/data/sched-test3 SCHED_PAT='tk0s-*' SCHED_TICK=2 SCHED_DEAD_S=15
A=/data/sched/alsched.py
rm -rf $SCHED_DIR; mkdir -p $SCHED_DIR; cd $SCHED_DIR
python3 $A daemon >daemon.log 2>&1 & DPID=$!
sleep 3
python3 $A wave --max-runtime 400s sleep 300 >w.out 2>&1
echo "bg rc=$? $(ls -t validity/*.json | head -1) $(ls -t validity/*.json | head -1 | xargs cat)" | tee -a bg.log
kill $DPID
