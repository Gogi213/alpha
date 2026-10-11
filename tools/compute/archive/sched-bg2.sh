#!/bin/bash
# TK-071: фон чужих операций диска по окнам 60 с (12 холостых окон) — эмпирическая верхняя граница при пачечном фоне; запуск как замер: systemd-run --unit tk071-bg2 --collect /data/benchrun.sh wave bash /data/sched/sched-bg2.sh
export SCHED_DIR=/data/sched-test4 SCHED_PAT='tk0s-*' SCHED_TICK=2 SCHED_DEAD_S=15
A=/data/sched/alsched.py
rm -rf $SCHED_DIR; mkdir -p $SCHED_DIR; cd $SCHED_DIR
python3 $A daemon >daemon.log 2>&1 & DPID=$!
sleep 3
for i in $(seq 1 12); do
  python3 $A wave --max-runtime 120s sleep 60 >w$i.out 2>&1
  python3 - <<PY | tee -a bg2.log
import json,glob,os
f=max(glob.glob("validity/*.json"),key=os.path.getmtime); d=json.load(open(f))
print("win$i", d["wall_s"], d["ios"], d["disk_b"], round(d["ios"]/d["wall_s"],3))
PY
done
kill $DPID
