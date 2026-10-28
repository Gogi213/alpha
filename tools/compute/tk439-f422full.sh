#!/bin/bash
# TK-439: tk422-features целиком (сентябрь, куб, 15 процессов) с --cube-range 1 + Neg — стена и побайтно против e2e/tk422-features.csv(+pairs) TK-436.
#   alsched submit --kind gate --cores 15 --name tk439-f422full2 --max-runtime 20m -- bash /data/tk0439/tk439-f422full.sh  (15 ядер по заявке — чистая стена; 1-ядерная заявка 1028072825489 шла при чужих 15 проц., 1137 с)
PY=/data/tk421/venv/bin/python3
R=/data/tk0436/e2e
O=/data/tk0439/f422full
rm -rf $O; mkdir -p $O
echo "load до: $(cut -d' ' -f1-3 /proc/loadavg)" > $O/load.txt
s=$(date +%s.%N)
$PY /data/tk0439/tk422-features.py $R/windows.csv $O/tk422-features.csv --cube-range 1 --procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/feat.log 2> $O/feat.err
e=$(date +%s.%N); echo "load после: $(cut -d' ' -f1-3 /proc/loadavg)" >> $O/load.txt; echo "full $(echo "$e - $s" | bc) s (прежний куб-код: 165,6 s)" >> $O/time.txt
ok=1
for f in "" ".pairs.csv"; do
  cmp $R/tk422-features.csv$f $O/tk422-features.csv$f > /dev/null 2>&1 && echo "OK tk422-features.csv$f" >> $O/time.txt || { echo "DIFF tk422-features.csv$f" >> $O/time.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/time.txt || echo DIFF >> $O/time.txt
echo 0 > $O/done
