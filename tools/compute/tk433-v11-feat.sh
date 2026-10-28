#!/bin/bash
# TK-433 v11: v9b-признаки на случайной выборке событий v11 (<=600/ячейку месяц x D x знак, сид 433), контроли ±30; сентябрь, затем август. Метка v11f.done, код v11f.rc.
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
rm -f ../v11f.done ../v11f.rc
for ym in 2026-09 2026-08; do
  mkdir -p f-$ym
  $PY tk433-features.py windows smp/legs-$ym.csv smp/legs-$ym.csv /srv/alpha-sda/candles-1m /srv/alpha-sda/candles-1m-aug f-$ym $ym > f-$ym/windows.log 2>&1 || { echo 1 > ../v11f.rc; exit 1; }
  $PY tk422-features.py f-$ym/windows.csv f-$ym/tk422-features.csv --procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv > f-$ym/feat.log 2> f-$ym/feat.err || { echo 2 > ../v11f.rc; exit 2; }
  $PY tk433-features.py merge f-$ym/windows.csv f-$ym/own.csv f-$ym/tk422-features.csv f-$ym/features-$ym.csv > f-$ym/merge.log 2>&1 || { echo 3 > ../v11f.rc; exit 3; }
done
echo 0 > ../v11f.rc
touch ../v11f.done
