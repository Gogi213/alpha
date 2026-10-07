#!/bin/bash
# tk065-gchain.sh: G2 (perf stat 3 пары голова/кандидат на d15 и d01, окно замера) и затем G1 (31 сутки марта производством через alsched). Лог /data/tk065/gchain.log, маркер gchain.done (после подачи G1).
A=alpha-b976c31-v3; B=alpha-93287fd-v3; L=/data/tk065/gchain.log; mkdir -p /data/tk065/g2 /data/tk065/g1; : > $L; rm -f /data/tk065/gchain.done
for m in d15 d01; do echo "G2 $m $(date +%T)" >> $L; /data/benchrun.sh stand bash /data/tk065/gridperf.sh $A $B /data/tk065/g2/$m 3 $m >> $L 2>&1; echo "rc $?" >> $L; done
echo "G1 submit $(date +%T)" >> $L
for i in $(seq 1 31); do d=2026-03-$(printf %02d $i)
  python3 /data/sched/alsched.py submit --cls prod --name tk065-g1-$d --max-runtime 4h --cores 1 --mem 3 -- bash /data/tk065/g1-day.sh $d alpha-93287fd-flag >> $L 2>&1
done
python3 /data/sched/alsched.py submit --cls prod --name tk065-g1-done --max-runtime 30h --cores 1 --mem 1 -- bash -c 'while [ $(grep -l "^done" /data/tk065/g1/2026-03-*/res.txt 2>/dev/null | wc -l) -lt 31 ]; do sleep 120; done; touch /data/tk065/g1.done' >> $L 2>&1
touch /data/tk065/gchain.done
