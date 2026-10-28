#!/bin/bash
# TK-436 (возврат Судьи п.3): контроли TK-433 (tk433-features windows) на КУБЕ против прежнего выхода с csv.gz (/data/tk0433/v11f/f-2026-09: windows.csv, own.csv, controls-diag.json);
# те же ноги smp/legs-2026-09.csv, --cut 1 авг. (история как у прежнего прогона, куб начинается с 24.07); побайтно + стена.
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0436/cgate
rm -rf $O; mkdir -p $O; rm -f /data/tk0436/cgate.rc
s=$(date +%s.%N)
$PY /data/tk0436/tk433-features.py windows smp/legs-2026-09.csv smp/legs-2026-09.csv - - $O 2026-09 --cube /data/tk0436/cube3 --cut 29759040 > $O/windows.log 2>&1
e=$(date +%s.%N)
echo "cube $(echo "$e - $s" | bc) s" > $O/time.txt
ok=1
for f in windows.csv own.csv controls-diag.json; do
  cmp f-2026-09/$f $O/$f > /dev/null 2>&1 && echo "OK $f" >> $O/time.txt || { echo "DIFF $f" >> $O/time.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/time.txt || echo DIFF >> $O/time.txt
ls -l --time-style=+%H:%M:%S f-2026-09/windows.log f-2026-09/windows.csv >> $O/time.txt
echo 0 > /data/tk0436/cgate.rc
