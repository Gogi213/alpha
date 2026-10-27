#!/bin/bash
cd /data/tk433
PY=/data/tk421/venv/bin/python3
rm -f trd.done trd.rc
for T in 5 15 60; do
  ( $PY tk433-trades.py e$T/end-start-th$T.csv out/legs-2026-09-th5.csv /data/tk417 e$T/trades-th$T.csv > e$T/trd.log 2>&1 && $PY tk433-endr.py e$T/end-start-th$T.csv e$T/trdr $T e$T/trades-th$T.csv > e$T/trdr.log 2>&1; echo $? > e$T/trd.rc ) &
done
wait
cat e5/trd.rc e15/trd.rc e60/trd.rc | sort -u | tr -d "\n" > trd.rc
touch trd.done
