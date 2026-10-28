#!/bin/bash
# TK-439: tk422-features с ленивой дамп-ориентацией (Neg) — срез сентября (≈680 окон), 1 процесс, побайтно против прежнего выхода f422prof/new.csv(+.pairs.csv).
#   alsched submit --kind gate --cores 1 --max-runtime 15m -- bash /data/tk0439/tk439-f422gate.sh  (флаг --cube-range 1: свечи группы окном минут)
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
R=/data/tk0436/f422prof
O=/data/tk0439/f422gate
rm -rf $O; mkdir -p $O
s=$(date +%s.%N)
$PY /data/tk0439/tk422-features.py $R/win.csv $O/new.csv --cube-range 1 --procs 1 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube /data/tk0436/cube3 --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/new.log 2> $O/new.err
e=$(date +%s.%N); echo "neg $(echo "$e - $s" | bc) s (прежний код, 1 проц., без cProfile: см. ниже)" >> $O/time.txt
ok=1
for f in "" ".pairs.csv"; do
  cmp $R/new.csv$f $O/new.csv$f > /dev/null 2>&1 && echo "OK new.csv$f" >> $O/time.txt || { echo "DIFF new.csv$f" >> $O/time.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/time.txt || echo DIFF >> $O/time.txt
echo 0 > $O/done
