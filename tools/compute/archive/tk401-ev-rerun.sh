#!/usr/bin/env bash
# TK-401 П-29, возврат Судьи 26.10 02:35: окно счёта 15.10–23.10 (D0 15.10 00:00 UTC; было 12–20.10). EVM 67 + Sol 31 одной заявкой, агрегат. Выход ev2/ ev-sol2/.
#   python3 /data/sched/alsched.py submit --cls prod --name tk401-evr --cores 15 --mem 40 --max-runtime 2h --est 70m --cwd /data/tk0393/tk401 -- bash /data/tk0393/tk401/rerun.sh
B=/data/tk0393/tk401; PY=/data/tk0380/npvenv/bin/python; export GEV=$B/gev2all
mkdir -p $B/ev2 $B/ev-sol2; rm -f $B/ev2/rerun.done
{ for c in $(ls $B/mid | sed 's/\.csv\.gz$//'); do echo "$c $B/mid/$c.csv.gz $B/ev2/$c"; done
  for c in $(ls $B/mid-sol | sed 's/\.csv\.gz$//'); do echo "$c $B/mid-sol/$c.csv.gz $B/ev-sol2/$c"; done; } \
 | xargs -P15 -L1 sh -c '[ -f $2.done ] && exit 0; '"$PY"' /data/tk0393/tk401-ev.py $0 $1 $2 > $2.log 2>&1; echo rc=$? >> $2.log; touch $2.done'
$PY /data/tk0393/tk401-ev-agg.py $B/ev-sol2/final evm=$B/ev2 sol=$B/ev-sol2 > $B/ev-sol2/final.txt 2>&1; echo "agg rc=$?" > $B/ev2/rerun.done
echo "монет evm $(ls $B/ev2/*-curve.csv|wc -l) sol $(ls $B/ev-sol2/*-curve.csv|wc -l) $(date +%T)" >> $B/ev2/rerun.done
