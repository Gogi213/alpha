#!/bin/bash
# tk065-wave-submit.sh <бинарник> [суток-файл]: заявки alsched на каждые сутки (1 ядро, 2 ГБ) + сборщик маркера /data/tk065/wave.done
B=${1:-alpha-b15pyr4pgoflag}; L=${2:-/data/tk065/days/days.tsv}; n=0
while read d M; do
  python3 /data/sched/alsched.py submit --cls prod --name tk065-w-$d --max-runtime 6h --cores 1 --mem 2 -- bash /data/tk065/wave-day.sh $d $M $B
  n=$((n+1))
done < $L
python3 /data/sched/alsched.py submit --cls prod --name tk065-wave-done --max-runtime 40h --cores 1 --mem 1 -- bash -c "while [ \$(ls /data/tk065/wt-*.done 2>/dev/null | wc -l) -lt $n ]; do sleep 300; done; touch /data/tk065/wave.done"
