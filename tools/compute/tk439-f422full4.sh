#!/bin/bash
# TK-439 (return п.2/3): tk422-features целиком (сентябрь, куб) с seq-кэшем вида (tk422-features-seq.py), умолчания (без --cube-range) — побайтно против e2e TK-436; ЦП задания — /data/sched/own/<id>/usage_usec.
#   python3 /data/sched/alsched.py submit --kind gate --cores 1 --max-runtime 30m --name tk439-f422full4 -- bash /data/tk0439/tk439-f422full4.sh   (gate = квота 1 ядро; чистая стена на 15 ядрах — когда calc свободен от tk440-cube)
PY=/data/tk421/venv/bin/python3
R=/data/tk0436/e2e
O=/data/tk0439/f422full4
rm -rf $O; mkdir -p $O
s=$(date +%s.%N)
$PY /data/tk0439/tk422-features-seq.py $R/windows.csv $O/tk422-features.csv --procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/feat.log 2> $O/feat.err
e=$(date +%s.%N); echo "full $(echo "$e - $s" | bc) s" >> $O/time.txt
ok=1
for f in "" ".pairs.csv"; do
  cmp $R/tk422-features.csv$f $O/tk422-features.csv$f > /dev/null 2>&1 && echo "OK tk422-features.csv$f" >> $O/time.txt || { echo "DIFF tk422-features.csv$f" >> $O/time.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/time.txt || echo DIFF >> $O/time.txt
echo 0 > $O/done
