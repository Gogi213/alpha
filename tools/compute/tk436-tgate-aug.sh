#!/bin/bash
# TK-436 (возврат Судьи п.2): гейт К4–К8 на августе — куб сделок авг. (cube3t_aug) + куб свечей cube3 (PAD=37, покрывает август) против прежнего tk433-trades.py на сырье /data/tk420/tr/bybit.
# Спот сделок Bybit за август на диске нет: и старый разбор (нет каталога bybit-spot), и куб (пустая заглушка) дают пусто → K4/K8 пусто в обоих.
cd /data/tk0436
PY=/data/tk421/venv/bin/python3
E=/data/tk433/aug/e5
L=/data/tk433/aug/out/legs-2026-08-th5.csv
rm -rf tgate_aug cubeAug; mkdir tgate_aug cubeAug; rm -f tgate_aug.rc
for f in cube3/bybit-linear.* cube3t_aug/trades-bybit-linear.*; do ln -s /data/tk0436/$f cubeAug/; done
echo _NONE_ > cubeAug/trades-bybit-spot.syms; echo 0 > cubeAug/trades-bybit-spot.lo
$PY -c "import numpy as np; np.save('cubeAug/trades-bybit-spot.npy', np.zeros((1, 6, 1)))"
s=$(date +%s.%N)
$PY tk433-trades.py $E/end-start-th5.csv $L /data/tk420/tr/bybit tgate_aug/old.csv > tgate_aug/old.log 2>&1
orc=$?
e=$(date +%s.%N)
$PY tk436-vec.py trades $E/end-start-th5.csv $L cubeAug tgate_aug/old.csv tgate_aug/new2.csv > tgate_aug/new2.log 2>&1
nrc=$?
f=$(date +%s.%N)
echo "старый $(echo "$e - $s" | bc) s rc $orc; вектор $(echo "$f - $e" | bc) s rc $nrc" > tgate_aug/time.txt
cmp tgate_aug/old.csv tgate_aug/new2.csv && echo IDENTICAL >> tgate_aug/time.txt || echo DIFF >> tgate_aug/time.txt
echo done > tgate_aug.rc
