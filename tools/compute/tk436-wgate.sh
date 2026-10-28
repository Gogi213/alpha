#!/bin/bash
# TK-436: гейт «было/стало» tk435-windows.py — csv.gz против куба (3 сут, все инструменты, X/D по умолчанию); выходы побайтно; стена обоих в wgate436/time.txt
cd /data/tk0435
PY=/data/tk421/venv/bin/python3
mkdir -p wgate436/old wgate436/new
rm -f wgate436/rc
s=$(date +%s.%N)
$PY tk435-windows.py events /srv/alpha-sda/candles-1m - - wgate436/old --procs 3 --t0 2026-09-10 --t1 2026-09-13 > wgate436/old.log 2>&1
m=$(date +%s.%N)
$PY tk435-windows.py events - - - wgate436/new --procs 3 --t0 2026-09-10 --t1 2026-09-13 --cube /data/tk0436/cube3 > wgate436/new.log 2>&1
e=$(date +%s.%N)
echo "old $(echo "$m - $s" | bc) s; new $(echo "$e - $m" | bc) s" > wgate436/time.txt
ok=1
for f in windows-2026-09-10_2026-09-13.csv funnel-2026-09-10_2026-09-13.json hist-2026-09-10_2026-09-13.json; do
  cmp wgate436/old/$f wgate436/new/$f || ok=0
done
[ $ok = 1 ] && echo IDENTICAL >> wgate436/time.txt || echo DIFF >> wgate436/time.txt
wc -l wgate436/old/windows-*.csv >> wgate436/time.txt
echo done > wgate436/rc
