#!/bin/bash
# TK-436: гейт «было/стало» К4–К8: tk433-trades.py (разбор сделок csv.gz) против tk436-vec.py trades (куб сделок по минуте) на v10 e5 сентября; строки «%.6g» сверяются
cd /data/tk0436
PY=/data/tk421/venv/bin/python3
E=/data/tk433/e5
rm -rf tgate; mkdir tgate; rm -f tgate.rc
cp /data/tk433/tk433-trades.py .
s=$(date +%s.%N)
$PY tk436-vec.py trades $E/end-start-th5.csv /data/tk433/out/legs-2026-09-th5.csv cube2 $E/trades-th5.csv tgate/new.csv > tgate/new.log 2>&1
rc=$?
m=$(date +%s.%N)
$PY tk433-trades.py $E/end-start-th5.csv /data/tk433/out/legs-2026-09-th5.csv /data/tk417 tgate/old.csv > tgate/old.log 2>&1
e=$(date +%s.%N)
echo "new $(echo "$m - $s" | bc) s; old $(echo "$e - $m" | bc) s; vec rc $rc" > tgate/time.txt
cmp $E/trades-th5.csv tgate/old.csv && echo "старый прогон == свежий «было»" >> tgate/time.txt || echo "старый прогон != свежий «было»" >> tgate/time.txt
cmp tgate/old.csv tgate/new.csv && echo IDENTICAL >> tgate/time.txt || echo DIFF >> tgate/time.txt
echo done > tgate.rc
