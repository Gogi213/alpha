#!/bin/bash
# TK-439 (return п.2): A/B на UAIUSDT (334 окна), 1 процесс, без cProfile: прежний код (tk422-features.py на calc до правки seq) против seq с кэшем вида; поочерёдно, чтобы не делить ядро.
#   python3 /data/sched/alsched.py submit --kind gate --cores 1 --max-runtime 15m --name tk439-f422ab2 -- bash /data/tk0439/tk439-f422ab2.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0439/f422ab2
rm -rf $O; mkdir -p $O
cp /data/tk0439/f422prof2/win.csv $O/win.csv
A="--procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00"
for v in old new old new; do
  F=/data/tk0439/tk422-features.py; [ $v = new ] && F=/data/tk0439/tk422-features-seq.py
  s=$(date +%s.%N); $PY $F $O/win.csv $O/$v.csv $A > $O/$v.log 2> $O/$v.err; e=$(date +%s.%N)
  echo "$v $(echo "$e - $s" | bc) cpu=$(grep usage_usec /sys/fs/cgroup/cpu.stat 2>/dev/null | cut -d' ' -f2)" >> $O/time.txt
done
for f in "" ".pairs.csv"; do cmp $O/old.csv$f $O/new.csv$f >/dev/null 2>&1 && echo "OK $f" >> $O/time.txt || echo "DIFF $f" >> $O/time.txt; done
echo 0 > $O/done
