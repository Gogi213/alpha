#!/bin/bash
# tk065-wave-submit.sh <бинарник> [суток-файл]: заявки alsched на каждые сутки (1 ядро, 2 ГБ) + сборщик маркера /data/tk065/wave.done
# TK-089 п.2: клетки суток (r2 + r2t) объявляются Летописи в GUARD_CELLS_DECLARED=/data/tk065/days/r2all-<сутки>.cells — done пишет их поклеточно
B=${1:-alpha-b15pyr4pgoflag}; L=${2:-/data/tk065/days/days.tsv}; n=0
while read d M; do
  cat /data/tk065/days/r2-$d.cells /data/tk065/days/r2t-$d.cells > /data/tk065/days/r2all-$d.cells
  GUARD_CELLS_DECLARED=/data/tk065/days/r2all-$d.cells python3 /data/sched/alsched.py submit --cls prod --name tk065-w-$d --max-runtime 6h --cores 1 --mem 2 -- bash /data/tk065/wave-day.sh $d $M $B
  n=$((n+1))
done < $L
python3 /data/sched/alsched.py submit --cls prod --name tk065-wave-done --max-runtime 40h --cores 1 --mem 1 -- bash -c "while [ \$(ls /data/tk065/wt-*.done 2>/dev/null | wc -l) -lt $n ]; do sleep 300; done; touch /data/tk065/wave.done"
