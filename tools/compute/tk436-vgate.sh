#!/bin/bash
# TK-436: гейт «было/стало» tk435-vol.py — csv.gz (--cut = первая минута куба) против куба, выходы побайтно; стена обоих в gate436/time.txt
cd /data/tk0435
PY=/data/tk421/venv/bin/python3
W=probe/windows-2026-09-10_2026-09-13.csv
LO=$(cat /data/tk0436/cube2/bybit-linear.lo)
mkdir -p gate436
s=$(date +%s.%N)
$PY tk435-vol.py $W 360 5 gate436/old.csv /srv/alpha-sda/candles-1m /srv/alpha-sda/candles-1m-aug --procs 4 --cut $LO > gate436/old.log 2>&1
m=$(date +%s.%N)
$PY tk435-vol.py $W 360 5 gate436/new.csv --cube /data/tk0436/cube2 --procs 4 > gate436/new.log 2>&1
e=$(date +%s.%N)
echo "old $(echo "$m - $s" | bc) s; new $(echo "$e - $m" | bc) s" > gate436/time.txt
if cmp gate436/old.csv gate436/new.csv; then echo IDENTICAL >> gate436/time.txt; else echo DIFF >> gate436/time.txt; fi
echo done > gate436/rc
