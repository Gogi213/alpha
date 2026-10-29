#!/bin/bash
# TK-441 возврат 5: шаг tk422 сентября на new7.py (отсортированный вид свечей при загрузке) + TK422_NUMBA=1, 15 процессов, на входах прогона 2 (e2e/f). w — прогрев кэша куба/свечей (+cmp), m — замер на тёплом кэше; cmp tk422-features.csv(+pairs) и features-2026-09.csv с принятыми.
#   python3 /data/sched/alsched.py submit --kind work --name tk441-e2e10 --cores 15 --max-runtime 20m --est 6m --mem 40 -- bash /data/tk0441/tk441-e2e10.sh
cd /data/tk0433/v11f || exit 9
NB=/data/tk0439/venv/bin/python3
export NUMBA_CACHE_DIR=/data/tk0441/nbcache TK422_NUMBA=1 TK441_T=1
I=/data/tk0441/e2e/f; O=/data/tk0441/e2e10
rm -rf $O; mkdir -p $O; rm -f /data/tk0441/e2e10.rc
md5sum /data/tk0441/new7.py > $O/md5.txt
A="--procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00"
ok=1
for v in w m; do
  mkdir -p $O/$v
  s=$(date +%s.%N)
  $NB /data/tk0441/new7.py $I/windows.csv $O/$v/tk422-features.csv $A > $O/$v/feat.log 2>&1 || { echo 5 > /data/tk0441/e2e10.rc; exit 5; }
  e=$(date +%s.%N); echo -e "tk422-$v\t$(echo "$e - $s" | bc)" >> $O/t.tsv
  s=$(date +%s.%N)
  /data/tk421/venv/bin/python3 /data/tk0436/tk433-features.py merge $I/windows.csv $I/own.csv $O/$v/tk422-features.csv $O/$v/features-2026-09.csv > $O/$v/merge.log 2>&1 || { echo 6 > /data/tk0441/e2e10.rc; exit 6; }
  e=$(date +%s.%N); echo -e "merge-$v\t$(echo "$e - $s" | bc)" >> $O/t.tsv
  for f in tk422-features.csv tk422-features.csv.pairs.csv; do
    cmp /data/tk0433/v11f/f-2026-09/$f $O/$v/$f > /dev/null 2>&1 && echo "OK $v $f" >> $O/cmp.txt || { echo "DIFF $v $f" >> $O/cmp.txt; ok=0; }
  done
  cmp /data/tk0433/v11f/g-2026-09/features-2026-09.csv $O/$v/features-2026-09.csv > /dev/null 2>&1 && echo "OK $v features-2026-09.csv" >> $O/cmp.txt || { echo "DIFF $v features-2026-09.csv" >> $O/cmp.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/cmp.txt || echo DIFF >> $O/cmp.txt
echo 0 > /data/tk0441/e2e10.rc
