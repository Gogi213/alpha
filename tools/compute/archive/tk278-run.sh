#!/bin/bash
# TK-278 п.1: R1 (r1x, r1col, r1old) + Э + К -> closes с монетой cap 0 + сверка с готовым. Итог — /data/tk0278/rc.txt (bad=<число наборов с расхождением/сбоем>).
W=/data/tk0278; cd $W || exit 9; : > timing.txt; rm -f rc.txt; bad=0
for s in k r1x r1col r1old e; do
  T=$(date +%s); bash $W/tk278-symclose.sh $s; [ -e $W/psim/$s.done ] || { bad=$((bad+1)); echo "$s FAIL" >> timing.txt; continue; }
  python3 $W/tk278-cmp.py $s >> timing.txt 2>&1 || bad=$((bad+1))
  echo "$s s=$(( $(date +%s)-T ))" >> timing.txt
done
echo "bad=$bad" > rc.txt; exit $bad
