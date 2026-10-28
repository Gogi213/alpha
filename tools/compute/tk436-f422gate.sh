#!/bin/bash
# TK-436 (возврат 3, п.1): tk422-features на кубе (--cube/--cube-trades) против прежнего пути (csv.gz) — срез сентября (каждое K-е окно), побайтно + стена.
#   alsched submit --kind gate --name tk436-f422gate --cores 3 --max-runtime 40m -- bash /data/tk0436/tk436-f422gate.sh
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0436/f422gate
K=${K:-12}
rm -rf $O; mkdir -p $O
awk -v k=$K 'NR==1||(NR-2)%k==0' f-2026-09/windows.csv > $O/win.csv
echo "окон в срезе: $(($(wc -l < $O/win.csv)-1)) из $(($(wc -l < f-2026-09/windows.csv)-1))" > $O/time.txt
COMMON="--procs 3 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv"
s=$(date +%s.%N)
$PY /data/tk0436/tk422-features.py $O/win.csv $O/new.csv $COMMON --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t > $O/new.log 2> $O/new.err
e=$(date +%s.%N); echo "cube $(echo "$e - $s" | bc) s" >> $O/time.txt
s=$(date +%s.%N)
$PY /data/tk0436/tk422-features.py $O/win.csv $O/old.csv $COMMON > $O/old.log 2> $O/old.err
e=$(date +%s.%N); echo "csv.gz $(echo "$e - $s" | bc) s" >> $O/time.txt
ok=1
for f in "" ".pairs.csv"; do
  cmp $O/old.csv$f $O/new.csv$f > /dev/null 2>&1 && echo "OK new.csv$f" >> $O/time.txt || { echo "DIFF new.csv$f" >> $O/time.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/time.txt || echo DIFF >> $O/time.txt
