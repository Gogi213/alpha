#!/bin/bash
# TK-071: шум чужих операций диска в окне — 2 буферных dd 1 ГБ и 3 холостых окна; запуск как замер: systemd-run --unit tk071-noise --collect /data/benchrun.sh wave bash /data/sched/sched-noise.sh
export SCHED_DIR=${SCHED_DIR:-/data/sched-test2} SCHED_PAT='tk0s-*' SCHED_TICK=2 SCHED_DEAD_S=15
A=/data/sched/alsched.py
rm -rf $SCHED_DIR; mkdir -p $SCHED_DIR; cd $SCHED_DIR
python3 $A daemon >daemon.log 2>&1 & DPID=$!
sleep 3
for i in 1 2; do
  python3 $A wave --max-runtime 120s dd if=/dev/sdb of=/dev/null bs=1M count=1024 skip=$((600000+i*3000)) >w$i.out 2>&1
  echo "buf$i rc=$? $(ls -t validity/*.json | head -1 | xargs cut -c1-330)" | tee -a noise.log
done
for i in 1 2 3; do
  python3 $A wave --max-runtime 60s sleep 9 >i$i.out 2>&1
  echo "idle$i rc=$? $(ls -t validity/*.json | head -1 | xargs cut -c1-330)" | tee -a noise.log
done
kill $DPID
