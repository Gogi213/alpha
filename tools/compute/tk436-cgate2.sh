#!/bin/bash
# TK-436 (возврат 2, п.2): контроли TK-433 — префиксные суммы присутствия + маски нормы + параллель по инструментам (--jobs 3) на кубе;
# побайтно против /data/tk0433/v11f/f-2026-09 (csv.gz, прежний код) + стена.
#   alsched submit --kind gate --name tk436-cgate2 --cores 3 --max-runtime 30m -- bash /data/tk0436/tk436-cgate2.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0436/cgate2
rm -rf $O; mkdir -p $O
s=$(date +%s.%N)
$PY /data/tk0436/tk433-features.py windows smp/legs-2026-09.csv smp/legs-2026-09.csv - - $O 2026-09 --cube /data/tk0436/cube3 --cut 29759040 --jobs 3 > $O/windows.log 2>&1
e=$(date +%s.%N)
echo "cube jobs=3 $(echo "$e - $s" | bc) s" > $O/time.txt
ok=1
for f in windows.csv own.csv controls-diag.json; do
  cmp f-2026-09/$f $O/$f > /dev/null 2>&1 && echo "OK $f" >> $O/time.txt || { echo "DIFF $f" >> $O/time.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/time.txt || echo DIFF >> $O/time.txt
