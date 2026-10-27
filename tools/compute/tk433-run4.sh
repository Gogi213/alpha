#!/bin/bash
# TK-433 v10: признаки конца/старта на трёх вариантах ноги T=5,15,60 (параллельно, по одному процессу на T)
cd /data/tk433
PY=/data/tk421/venv/bin/python3
rm -f end.done end.rc
for T in 5 15 60; do
  mkdir -p e$T
  ( $PY tk433-end.py out/legs-2026-09-th$T.csv $T /srv/alpha-sda/candles-1m /srv/alpha-sda/candles-1m-aug e$T > e$T/run.log 2>&1; echo $? > e$T/rc ) &
done
wait
cat e5/rc e15/rc e60/rc | sort -u | tr -d "\n" > end.rc
touch end.done
