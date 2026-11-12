#!/usr/bin/env bash
# TK-401: разбор свопов Sol swaps3 (пара vault'ов) суток 15..22.10, по 2 параллельно (~4,5 ГБ на сутки). Только сырьё (свопы), без посекундного слоя (CEO 22:37).
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7; O=$S/tk401; cd "$S" || exit 1
rm -f $O/sol3.done $O/sol3.fail $O/s3/fail.txt; mkdir -p $O/s3
for d in 15 16 17 18 19 20 21 22; do
  [ -f $O/s3/$d.done ] || echo $d
done | xargs -r -P 2 -I{} sh -c 'SRC=gate30k python3 tk378-sol-swaps3.py mints-tk393-sol.csv '$O'/s3/{}-swaps.csv '$O'/s3/{}-cov.csv '$R'/sol/seg/202610{}-*.csv > '$O'/s3/{}.log 2>&1 && touch '$O'/s3/{}.done || echo FAIL {} >> '$O'/s3/fail.txt'
if [ -s $O/s3/fail.txt ]; then cp $O/s3/fail.txt $O/sol3.fail; exit 1; fi
echo done > $O/sol3.done
