#!/bin/bash
# TK-441: полный гейт tk422-features сентября (все окна e2e TK-439), 1 процесс (gate = квота 1 ядро): /data/tk0441/new.py против принятого /data/tk0433/v11f/f-2026-09/tk422-features.csv (+ .pairs.csv).
#   python3 /data/sched/alsched.py submit --kind gate --cores 1 --max-runtime 70m --name tk441-full -- bash /data/tk0441/tk441-full.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0441/full
rm -rf $O; mkdir -p $O
A="--procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00"
s=$(date +%s.%N)
$PY /data/tk0441/new.py /data/tk0433/v11f/f-2026-09/windows.csv $O/new.csv $A > $O/new.log 2> $O/new.err; rc=$?
e=$(date +%s.%N); echo "new rc=$rc $(echo "$e - $s" | bc)" > $O/res.txt
for f in "" ".pairs.csv"; do cmp /data/tk0433/v11f/f-2026-09/tk422-features.csv$f $O/new.csv$f >/dev/null 2>&1 && echo "OK $f" >> $O/res.txt || echo "DIFF $f" >> $O/res.txt; done
