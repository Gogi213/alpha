#!/bin/bash
# TK-436 (возврат 3, п.3): tk422-features после кэша orient/read_5m — срез сентября K=12 (3 процесса), побайтно против old.csv прежнего гейта (код до правки, путь csv.gz).
#   alsched submit --kind gate --name tk436-f422gate2 --cores 3 --max-runtime 30m -- bash /data/tk0436/tk436-f422gate2.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
R=/data/tk0436/f422ref
O=/data/tk0436/f422gate2
if [ ! -f $R/old.csv ]; then mkdir -p $R; cp /data/tk0436/f422gate/old.csv /data/tk0436/f422gate/old.csv.pairs.csv /data/tk0436/f422gate/win.csv $R/; fi
rm -rf $O; mkdir -p $O
s=$(date +%s.%N)
$PY /data/tk0436/tk422-features.py $R/win.csv $O/new.csv --procs 3 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/new.log 2> $O/new.err
e=$(date +%s.%N); echo "cube $(echo "$e - $s" | bc) s (прежний код: 198 s)" >> $O/time.txt
ok=1
for f in "" ".pairs.csv"; do
  cmp $R/old.csv$f $O/new.csv$f > /dev/null 2>&1 && echo "OK new.csv$f" >> $O/time.txt || { echo "DIFF new.csv$f" >> $O/time.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/time.txt || echo DIFF >> $O/time.txt
