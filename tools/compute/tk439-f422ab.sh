#!/bin/bash
# TK-439: A/B одной группы (UAIUSDT, 334 окна): старый код против нового (+Neg, +--cube-range 1), 1 процесс каждый, одновременно.
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
R=/data/tk0436/e2e
O=/data/tk0439/f422ab
rm -rf $O; mkdir -p $O
head -1 $R/windows.csv > $O/win.csv; grep ',UAIUSDT,' $R/windows.csv >> $O/win.csv
A="--procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00"
( s=$(date +%s.%N); $PY /data/tk0439/tk422-old.py $O/win.csv $O/old.csv $A > $O/old.log 2> $O/old.err; e=$(date +%s.%N); echo "old $(echo "$e - $s" | bc)" >> $O/time.txt ) &
( s=$(date +%s.%N); $PY /data/tk0439/tk422-features.py $O/win.csv $O/new.csv --cube-range 1 $A > $O/new.log 2> $O/new.err; e=$(date +%s.%N); echo "new $(echo "$e - $s" | bc)" >> $O/time.txt ) &
wait
for f in "" ".pairs.csv"; do cmp $O/old.csv$f $O/new.csv$f >/dev/null 2>&1 && echo "OK $f" >> $O/time.txt || echo "DIFF $f" >> $O/time.txt; done
echo 0 > $O/done
