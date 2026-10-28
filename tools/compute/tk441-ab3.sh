#!/bin/bash
# TK-441 круг 3: A/B на UAIUSDT (298 окон, 1 процесс): new2.py (numba corr/fractals) против new3.py (+ hl(): max/min по срезам numpy, обе с TK422_NUMBA=1), поочерёдно; d0 — new3.py без флага (умолчание); cmp .csv и .pairs.csv с ab2/old.csv.
#   python3 /data/sched/alsched.py submit --kind gate --cores 1 --max-runtime 15m --name tk441-ab3 -- bash /data/tk0441/tk441-ab3.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk0439/venv/bin/python
O=/data/tk0441/ab3
rm -rf $O; mkdir -p $O
cp /data/tk0439/f422prof2/win.csv $O/win.csv
export NUMBA_CACHE_DIR=/data/tk0441/nbcache
A="--procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00"
for v in b1 c1 b2 c2 c3 d0; do
  F=/data/tk0441/new2.py; N=1; [ ${v:0:1} = c ] && F=/data/tk0441/new3.py; [ $v = d0 ] && { F=/data/tk0441/new3.py; N=0; }
  s=$(date +%s.%N); TK422_NUMBA=$N $PY $F $O/win.csv $O/$v.csv $A > $O/$v.log 2> $O/$v.err; e=$(date +%s.%N)
  echo "$v $(echo "$e - $s" | bc)" >> $O/time.txt
done
for v in c1 c3 d0; do for f in "" ".pairs.csv"; do cmp /data/tk0441/ab2/old.csv$f $O/$v.csv$f >/dev/null 2>&1 && echo "OK $v $f" >> $O/time.txt || echo "DIFF $v $f" >> $O/time.txt; done; done
echo 0 > $O/done
