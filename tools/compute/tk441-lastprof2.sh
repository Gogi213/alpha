#!/bin/bash
# TK-441 возврат 3: повтор замера A (tk422-features, срез 677 окон, 1 процесс) на тёплом кэше, к функциям добавлен _sqe (построение сортированного вида) — делит seq на «построение» и «срез».
#   python3 /data/sched/alsched.py submit --kind work --name tk441-lastprof2 --cores 1 --max-runtime 10m --est 2m --mem 20 -- bash /data/tk0441/tk441-lastprof2.sh
cd /data/tk0433/v11f || exit 9
NB=/data/tk0439/venv/bin/python3
export NUMBA_CACHE_DIR=/data/tk0441/nbcache TK422_NUMBA=1
C=/data/tk0436/cube3
O=/data/tk0441/lastprof2
rm -rf $O; mkdir -p $O
sed -e "s/'trades_min_cube'):/'trades_min_cube', '_sqe', 'day_ent', 'ob_series', 'trades_min'):/" -e "s#/data/tk0441/lastprof/a-tm.txt#$O/a-tm.txt#" /data/tk0441/lastprof/a.py > $O/a.py
s=$(date +%s.%N)
$NB $O/a.py /data/tk0436/f422prof/win.csv $O/a.csv --procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube $C --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/a.log 2> $O/a.err
e=$(date +%s.%N); echo "a wall $(echo "$e - $s" | bc)" >> $O/time.txt
cmp /data/tk0436/f422prof/new.csv $O/a.csv >/dev/null 2>&1 && echo "OK a.csv" >> $O/time.txt || echo "DIFF a.csv" >> $O/time.txt
echo 0 > $O/done
