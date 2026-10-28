#!/bin/bash
# TK-439 (return п.1): профиль tk422-features на крупной группе UAIUSDT (334 окна), 1 процесс, cProfile; одна таблица tottime.
#   python3 /data/sched/alsched.py submit --kind work --cores 1 --max-runtime 15m -- bash /data/tk0439/tk439-f422prof2.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
R=/data/tk0436/e2e
O=/data/tk0439/f422prof2
rm -rf $O; mkdir -p $O
head -1 $R/windows.csv > $O/win.csv; grep ',UAIUSDT,' $R/windows.csv >> $O/win.csv
$PY -m cProfile -o $O/prof.bin /data/tk0439/tk422-features.py $O/win.csv $O/new.csv --procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/new.log 2> $O/new.err
$PY -c "
import pstats
p=pstats.Stats('$O/prof.bin'); p.sort_stats('tottime').print_stats(30)" > $O/top-tottime.txt 2>&1
cmp $O/new.csv /data/tk0439/f422ab/new.csv && echo SAME > $O/cmp.txt || echo DIFF > $O/cmp.txt
echo 0 > $O/done
