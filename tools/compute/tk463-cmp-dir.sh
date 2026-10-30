#!/bin/bash
# TK-463: перегон cmp (биты f64) + побайтовое сравнение для готовых пар <p>.py.csv.gz / <p>.rs.csv.gz в каталоге.
# bash tk463-cmp-dir.sh <каталог> <монеты через запятую> [ТФ через запятую=60,240,1440]; итог — строки в <каталог>/gate2.log
D=$1; COINS=${2//,/ }; TFS=${3:-60,240,1440}; PY=/data/tk421/venv/bin/python
: > "$D/gate2.log"
for c in $COINS; do for tf in ${TFS//,/ }; do p=$D/$c-$tf
  if cmp -s <(zcat $p.py.csv.gz) <(zcat $p.rs.csv.gz); then b=BYTES_EQ; else b=BYTES_DIFF; fi
  r=$($PY /data/tk0463/tk463_gate.py cmp $p.py.csv.gz $p.rs.csv.gz 2>&1 | tail -2 | tr '\n' ' ')
  echo "d2b $c $tf $b :: $r" >> "$D/gate2.log"
done; done
echo "pairs done $(grep -c IDENTICAL $D/gate2.log) of $(wc -l < $D/gate2.log), bytes_eq $(grep -c BYTES_EQ $D/gate2.log)" > "$D/gate2.DONE"
