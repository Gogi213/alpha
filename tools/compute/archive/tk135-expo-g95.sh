#!/bin/bash
# TK-135/С-64: p12-r3-expo.py (до aac4bffd^) против p12-r2-expo.py --g95 на реальных rounds одного месяца (янв, p12r3a), md5 выходов
set -e
W=/data/tk135gate4/expo-g95; SRC=${SRC:-/data/p12r3a}; MO=${MO:-2026-01}
rm -rf $W/root $W/out; mkdir -p $W/root $W/out
for d in v-b vt-b s-v s-vt; do mkdir -p $W/root/$d; ln -s $SRC/$d/$MO $W/root/$d/$MO; done
cd $W
PYTHONHASHSEED=0 EXPO_OUT=$W/out/old.json python3 /data/tk135gate4/old/tools/compute/p12-r3-expo.py $W/root > out/old.stdout 2>&1; echo old rc=$?
PYTHONHASHSEED=0 python3 /data/tk135gate4/new/tools/compute/p12-r2-expo.py $W/root --g95 --out $W/out/new.json > out/new.stdout 2>&1; echo new rc=$?
{ md5sum out/old.json out/new.json out/old.stdout out/new.stdout; } > $W/md5.txt
cmp out/old.json out/new.json && echo EQUAL_JSON; cmp out/old.stdout out/new.stdout && echo EQUAL_STDOUT
echo done > $W/done
