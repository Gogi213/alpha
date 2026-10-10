#!/bin/bash
# tk084-p12-submit.sh [бинарник]: генерирует посуточные скрипты 9 клеток П-12 по суткам пула R2 (/data/tk065/days/days.tsv) и ставит заявки alsched (1 ядро, MEM ГБ на сутки, по умолчанию 8: пик тяжёлых суток > 4,2 ГБ) + маркер /data/tk084/p12.done
B=${1:-alpha-tk084-dl}; L=/data/tk065/days/days.tsv; out=/data/tk084/days; mkdir -p $out; n=0
while read d M; do
  J=/data/tk046/$M/home/alpha/tmp-p07/cells-by-day/jall-$M-$d.sh
  [ -e "$J" ] || { echo "нет $J" >&2; continue; }
  python3 /data/tk084/p12-gen.py $J $d $out > /dev/null || exit 1
  n=$((n+1)); [ -e /data/tk084/w-$d.done ] && continue
  export GUARD_INPUTS=/data/tk046/$M/home/alpha/epochs/e-$M/study/root-$d GUARD_CELLS_DECLARED=/data/tk084/days/p12-$d.cells
  python3 /data/sched/alsched.py submit --cls prod --name tk084-p12-$d --max-runtime 3h --cores 1 --mem ${MEM:-8} -- bash /data/tk084/p12-day.sh $d $M $B
done < $L
python3 /data/sched/alsched.py submit --cls prod --name tk084-p12-done --max-runtime 40h --cores 1 --mem 1 -- bash -c "while [ \$(ls /data/tk084/w-*.done 2>/dev/null | wc -l) -lt $n ]; do sleep 300; done; touch /data/tk084/p12.done"
echo "заявок суток: $n"
