#!/bin/bash
# TK-441: A/B на UAIUSDT (298 окон), 1 процесс: /data/tk0439/tk422-features-seq.py (принятый) против /data/tk0441/new.py, поочерёдно; cmp .csv и .pairs.csv.
#   python3 /data/sched/alsched.py submit --kind gate --cores 1 --max-runtime 15m --name tk441-ab -- bash /data/tk0441/tk441-ab.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0441/ab
rm -rf $O; mkdir -p $O
cp /data/tk0439/f422prof2/win.csv $O/win.csv
A="--procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00"
for v in old new old new; do
  F=/data/tk0439/tk422-features-seq.py; [ $v = new ] && F=/data/tk0441/new.py
  s=$(date +%s.%N); $PY $F $O/win.csv $O/$v.csv $A > $O/$v.log 2> $O/$v.err; e=$(date +%s.%N)
  echo "$v $(echo "$e - $s" | bc)" >> $O/time.txt
done
for f in "" ".pairs.csv"; do cmp $O/old.csv$f $O/new.csv$f >/dev/null 2>&1 && echo "OK $f" >> $O/time.txt || echo "DIFF $f" >> $O/time.txt; done
echo 0 > $O/done
