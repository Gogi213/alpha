#!/bin/bash
# tk084-p12x-submit.sh [бинарник]: 9 клеток П-12 по XAU/CL на 208 суток b14x (/data/tk048/tk040-b14x/days.txt), заявки alsched (1 ядро, 4 ГБ на сутки) + маркер /data/tk084/p12x.done
B=${1:-alpha-tk084-dl}; L=/data/tk048/tk040-b14x/days.txt; out=/data/tk084/daysx; mkdir -p $out; n=0
while read M d; do
  J=/data/tk084/x/$M/home/alpha/tmp-p07/cells-by-day/jall-$M-$d.sh
  [ -e "$J" ] || { echo "нет $J" >&2; continue; }
  python3 /data/tk084/p12-gen.py $J $d $out > /dev/null || exit 1
  n=$((n+1)); [ -e /data/tk084/wx-$d.done ] && continue
  export GUARD_INPUTS=/data/tk084/x/$M/home/alpha/epochs/e-$M/study/root-$d GUARD_CELLS_DECLARED=$out/p12-$d.cells
  python3 /data/sched/alsched.py submit --cls prod --name tk084-p12x-$d --max-runtime 2h --cores 1 --mem ${MEM:-4} -- bash /data/tk084/p12x-day.sh $d $M $B
done < $L
python3 /data/sched/alsched.py submit --cls prod --name tk084-p12x-done --max-runtime 40h --cores 1 --mem 1 -- bash -c "while [ \$(ls /data/tk084/wx-*.done 2>/dev/null | wc -l) -lt $((n-1)) ]; do sleep 300; done; touch /data/tk084/p12x.done"
echo "заявок суток: $n"
