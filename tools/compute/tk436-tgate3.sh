#!/bin/bash
# TK-436: гейт К4–К8 v3: куб сделок с max/min цены сделки по минуте (пик K7 из сделок, не из high свечи); сверка побайтно с tgate/old.csv (859 с)
cd /data/tk0436
PY=/data/tk421/venv/bin/python3
E=/data/tk433/e5
rm -rf tgate3 cube3t; mkdir tgate3 cube3t; rm -f tgate3.rc
for f in cube2/bybit-linear.* ; do ln -s /data/tk0436/$f cube3t/; done
s=$(date +%s.%N)
$PY tk436-cube.py trades 2026-09 /data/tk417 cube3t 3 > tgate3/cube.log 2>&1
m=$(date +%s.%N)
$PY tk436-vec.py trades $E/end-start-th5.csv /data/tk433/out/legs-2026-09-th5.csv cube3t $E/trades-th5.csv tgate3/new.csv > tgate3/new.log 2>&1
rc=$?
e=$(date +%s.%N)
echo "куб сделок $(echo "$m - $s" | bc) s; вектор $(echo "$e - $m" | bc) s; rc $rc; было 859 s" > tgate3/time.txt
cmp tgate/old.csv tgate3/new.csv && echo IDENTICAL >> tgate3/time.txt || echo DIFF >> tgate3/time.txt
echo done > tgate3.rc
