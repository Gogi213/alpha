#!/bin/bash
# TK-515: G-L1b — ядро на DOGE 17.09 (Binance × OKX, L=500): полная книга против L1-файлов (после l1_gate.sh, каталоги $T/{bn,okx}/l1), оба с P40_L1=1.
# Проверки: проход 1 sha прежний; признаки (кроме l1_notional/bookimb1) байт в байт с прогоном без флага; l1_notional/bookimb1 полная = L1; fills/exits — таблица расхождений с классом (l1_diff.py).
# Запуск на srv: bash /root/p40core-tk515/l1_gate_b.sh   (база без флагов — /tmp/old.csv*, снята прежним бинарём)
Q=${Q:-/root/p40core-tk515}; C=$Q/rs/target/release/p40core; T=${T:-/tmp/l1_gate}; W=${W:-/tmp/l1_gate_b}; SHA1=${SHA1:-121e8403ba03}
mkdir -p $W; rm -f $W/*
TR="/dev/shm/p40/TA /dev/shm/p40/TB 12300 12.3 500"
export P40_L1=1 P40_L1_STAT=1
nice -n 19 $C $W/full.csv /dev/shm/p40/A /dev/shm/p40/B $TR 2>&1 | tail -1
nice -n 19 $C $W/l1.csv $T/bn/l1 $T/okx/l1 $TR 2>&1 | tail -1
fail=0
for n in full l1; do s=$(sha256sum < $W/$n.csv | cut -c1-12); echo "проход 1 $n: sha $s"; [ "$s" = "$SHA1" ] || { echo "ПРОХОД 1: sha не $SHA1"; fail=1; }; done
cmp $W/full.csv.features.csv $W/l1.csv.features.csv && echo "признаки полная = L1: байт в байт (все колонки, вкл. l1_notional/bookimb1)" || fail=1
python3 - <<PY || fail=1
import csv, sys
o = list(csv.reader(open("/tmp/old.csv.features.csv", newline="")))
n = list(csv.reader(open("$W/l1.csv.features.csv", newline="")))
print("колонки old:", o[0]); print("колонки L1 :", n[0])
same = [c for c in o[0] if c in n[0]]
bad = []
for c in same:
    i, j = o[0].index(c), n[0].index(c)
    if [r[i] for r in o] != [r[j] for r in n]:
        bad.append(c)
print("общие колонки:", same); print("различаются со старой (без флага):", bad)
sys.exit(1 if bad else 0)
PY
python3 $Q/l1_diff.py $W/full.csv $W/l1.csv || fail=1
exit $fail
