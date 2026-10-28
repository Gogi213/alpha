#!/bin/bash
# TK-439: профиль tk422-features ПОСЛЕ кэша orient/read_5m — срез сентября (каждое 4-е окно, ≈680), 1 процесс, cProfile.
#   alsched submit --kind work --cores 1 --max-runtime 15m -- bash /data/tk0439/tk439-f422prof.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0439/f422prof
rm -rf $O; mkdir -p $O
cp /data/tk0436/f422prof/win.csv $O/win.csv
$PY -m cProfile -o $O/prof.bin /data/tk0436/tk422-features.py $O/win.csv $O/new.csv --procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/new.log 2> $O/new.err
$PY -c "
import pstats
p=pstats.Stats('$O/prof.bin'); p.sort_stats('tottime').print_stats(25)" > $O/top-tottime.txt 2>&1
$PY -c "
import pstats
p=pstats.Stats('$O/prof.bin'); p.sort_stats('cumtime').print_stats(30)" > $O/top-cumtime.txt 2>&1
cmp $O/new.csv /data/tk0436/f422prof/new.csv && echo SAME > $O/cmp.txt || echo DIFF > $O/cmp.txt
echo 0 > $O/done
