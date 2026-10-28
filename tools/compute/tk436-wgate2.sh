#!/bin/bash
# TK-436: гейт векторизованного детектора окон — новый код (куб) против готовых выходов старого (wgate436/old, csv.gz); побайтно + стена
cd /data/tk0435
PY=/data/tk421/venv/bin/python3
mkdir -p wgate436/new2
rm -f wgate436/rc2
s=$(date +%s.%N)
$PY tk435-windows.py events - - - wgate436/new2 --procs 3 --t0 2026-09-10 --t1 2026-09-13 --cube /data/tk0436/cube3 > wgate436/new2.log 2>&1
e=$(date +%s.%N)
echo "new2 $(echo "$e - $s" | bc) s" > wgate436/time2.txt
ok=1
for f in windows-2026-09-10_2026-09-13.csv funnel-2026-09-10_2026-09-13.json hist-2026-09-10_2026-09-13.json; do
  cmp wgate436/old/$f wgate436/new2/$f || ok=0
done
[ $ok = 1 ] && echo IDENTICAL >> wgate436/time2.txt || echo DIFF >> wgate436/time2.txt
cat wgate436/time.txt >> wgate436/time2.txt
echo done > wgate436/rc2
