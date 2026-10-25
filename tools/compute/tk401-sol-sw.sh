#!/usr/bin/env bash
# TK-401: разбор свопов Sol суток 21 и 22.10 последовательно (по одному, ~4,5 ГБ < заявки 8 ГБ; Судья 25.10 19:50, oom)
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd7; cd "$S" || exit 1
rm -f "$R/SOL2122.done" "$R/SOL2122.fail" "$R/sol/sw/fail.txt"
for d in 20261021 20261022; do
  SRC=gate30k python3 tk378-sol-swaps2.py mints-tk393-sol.csv $R/sol/sw/$d-swaps.csv $R/sol/sw/$d-cov.csv $R/sol/seg/$d-*.csv > $R/sol/sw/$d.log 2>&1 || echo FAIL $d >> $R/sol/sw/fail.txt
done
if [ -s "$R/sol/sw/fail.txt" ]; then echo "fail rc: $(cat $R/sol/sw/fail.txt)" > "$R/SOL2122.fail"; exit 1; fi
echo done > "$R/SOL2122.done"
