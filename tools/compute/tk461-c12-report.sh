#!/bin/bash
# TK-461: ячейки/годы/Холм по контролям C1, C2 Санчо (выход TK-470, формат d2b-out) -- всё время и часть «проверка» (§8).
# Заявка: python3 /data/sched/alsched.py submit --cls prod --name tk461-c12-rep --cores 5 --mem 24 --est 20m --max-runtime 40m -- bash /data/tk0461/tk461-c12-report.sh
cd /data/tk0461
PY=/data/tk421/venv/bin/python
export TK469_REPORT_FAST=1
O=/data/tk0461/c12-rep
mkdir -p $O
rm -f $O/DONE $O/FAIL
for c in c1 c2; do
  for part in all check; do
    P=$part; [ $part = all ] && P=""
    TK461_PART=$P $PY tk461_d2b_report.py /data/tk0461/c12-out/$c $O/$c-$part-cells.csv $O/$c-$part-years.csv 5 "*.csv.gz" 163923 > $O/$c-$part.log 2>&1 || { echo "fail $c $part" > $O/FAIL; exit 1; }
  done
done
touch $O/DONE
