#!/bin/bash
# tk065-wave-submit-a.sh <бинарник>: пересчёт семей A по всем суткам — заявки alsched (1 ядро, 2 ГБ), маркер /data/tk065/wavea.done
# выход /data/tk065/wa-<сутки>/b5/r2a/<сутки>; сначала python3 /data/tk065/r2a-gen.py /data/tk065/days
B=${1:?бинарник}; n=0
while read d M; do
  GUARD_CELLS_DECLARED=/data/tk065/days/r2a-$d.cells python3 /data/sched/alsched.py submit --cls prod --name tk065-wa-$d --max-runtime 6h --cores 1 --mem 2 -- bash -c "SCR=r2a bash /data/tk065/probe.sh wa-$d $B $M $d"
  n=$((n+1))
done < /data/tk065/days/days.tsv
python3 /data/sched/alsched.py submit --cls prod --name tk065-wavea-done --max-runtime 40h --cores 1 --mem 1 -- bash -c "while [ \$(ls /data/tk065/wa-*.done 2>/dev/null | wc -l) -lt $n ]; do sleep 300; done; touch /data/tk065/wavea.done"
