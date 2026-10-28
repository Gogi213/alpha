#!/bin/bash
# TK-441 круг 3: шаг tk422 сентября на new6.py (метки TK441_T) + TK422_NUMBA=1 (numba corr/fractals + hl() max/min срезами numpy), 15 процессов, на готовых входах прогона 2 (e2e/f); cmp tk422-features.csv(+pairs) и features-2026-09.csv с принятыми.
#   python3 /data/sched/alsched.py submit --kind work --name tk441-e2e6 --cores 15 --max-runtime 15m --est 3m --mem 40 -- bash /data/tk0441/tk441-e2e6.sh
cd /data/tk0433/v11f || exit 9
NB=/data/tk0439/venv/bin/python3
export NUMBA_CACHE_DIR=/data/tk0441/nbcache TK422_NUMBA=1 TK441_T=1
I=/data/tk0441/e2e/f; O=/data/tk0441/e2e6
rm -rf $O; mkdir -p $O; rm -f /data/tk0441/e2e6.rc
T0=$(date +%s.%N); t() { echo -e "$1\t$(echo "$(date +%s.%N) - $T0" | bc)" >> $O/t.tsv; }
$NB /data/tk0441/new6.py $I/windows.csv $O/tk422-features.csv --procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/feat.log 2>&1 || { echo 5 > /data/tk0441/e2e6.rc; exit 5; }; t tk422
/data/tk421/venv/bin/python3 /data/tk0436/tk433-features.py merge $I/windows.csv $I/own.csv $O/tk422-features.csv $O/features-2026-09.csv > $O/merge.log 2>&1 || { echo 6 > /data/tk0441/e2e6.rc; exit 6; }; t merge
ok=1
for f in tk422-features.csv tk422-features.csv.pairs.csv; do
  cmp /data/tk0433/v11f/f-2026-09/$f $O/$f > /dev/null 2>&1 && echo "OK $f" >> $O/cmp.txt || { echo "DIFF $f" >> $O/cmp.txt; ok=0; }
done
cmp /data/tk0433/v11f/g-2026-09/features-2026-09.csv $O/features-2026-09.csv > /dev/null 2>&1 && echo "OK features-2026-09.csv" >> $O/cmp.txt || { echo "DIFF features-2026-09.csv" >> $O/cmp.txt; ok=0; }
[ $ok = 1 ] && echo IDENTICAL >> $O/cmp.txt || echo DIFF >> $O/cmp.txt
echo 0 > /data/tk0441/e2e6.rc
