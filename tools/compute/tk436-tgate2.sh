#!/bin/bash
# TK-436: гейт К4–К8 v2: куб сделок пересобран (mawk печатал суммы %.6g — теперь %.17g), сверка побайтно с tgate/old.csv (свежее «было», 859 с)
cd /data/tk0436
PY=/data/tk421/venv/bin/python3
E=/data/tk433/e5
rm -rf tgate2 cube2t; mkdir tgate2 cube2t; rm -f tgate2.rc
for f in cube2/bybit-linear.* ; do ln -s /data/tk0436/$f cube2t/; done
s=$(date +%s.%N)
$PY tk436-cube-fix.py trades 2026-09 /data/tk417 cube2t 3 > tgate2/cube.log 2>&1
m=$(date +%s.%N)
$PY tk436-vec.py trades $E/end-start-th5.csv /data/tk433/out/legs-2026-09-th5.csv cube2t $E/trades-th5.csv tgate2/new.csv > tgate2/new.log 2>&1
rc=$?
e=$(date +%s.%N)
echo "куб сделок $(echo "$m - $s" | bc) s; вектор $(echo "$e - $m" | bc) s; rc $rc; было 859 s" > tgate2/time.txt
cmp tgate/old.csv tgate2/new.csv && echo IDENTICAL >> tgate2/time.txt || echo DIFF >> tgate2/time.txt
echo done > tgate2.rc
