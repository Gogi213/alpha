#!/bin/bash
# TK-441 возврат 4: tk422-features — отсортированный вид свечей (seq/_sqe) готов при загрузке из куба, а не строится заново на группу. new7.py (вид при загрузке) против new6.py (прежний, 70d59181) на срезе 677 окон, 1 процесс, TK422_NUMBA=1, тёплый кэш; w — прогрев new7 (+cmp), далее b/c поочерёдно; cmp .csv и .pairs.csv с эталоном среза /data/tk0436/f422prof/new.csv
#   python3 /data/sched/alsched.py submit --kind work --name tk441-sq --cores 1 --max-runtime 25m --est 10m --mem 20 -- bash /data/tk0441/tk441-sq.sh
cd /data/tk0433/v11f || exit 9
NB=/data/tk0439/venv/bin/python3
export NUMBA_CACHE_DIR=/data/tk0441/nbcache TK422_NUMBA=1
O=/data/tk0441/sq
rm -rf $O; mkdir -p $O
A="--procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00"
for v in w b1 c1 b2 c2; do
  F=/data/tk0441/new6.py; [ ${v:0:1} = c ] || [ $v = w ] && F=/data/tk0441/new7.py
  s=$(date +%s.%N); $NB $F /data/tk0436/f422prof/win.csv $O/$v.csv $A > $O/$v.log 2> $O/$v.err; rc=$?; e=$(date +%s.%N)
  echo "$v rc=$rc $(echo "$e - $s" | bc)" >> $O/time.txt
  for f in "" ".pairs.csv"; do cmp /data/tk0436/f422prof/new.csv$f $O/$v.csv$f >/dev/null 2>&1 && echo "OK $v $f" >> $O/time.txt || echo "DIFF $v $f" >> $O/time.txt; done
done
echo 0 > $O/done
