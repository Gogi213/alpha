#!/bin/bash
# TK-409: перезапуск формы (б) для монет, упавших на записи counts (ValueError drop_norev, исправлено в tk409-ev-conv.py), + практики Solana вне coins.lst R; V=1000, 15 ядер
B=/data/tk0393/tk409/Sb; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all PY S B
cd $B; rm -f sbfix.done
grep -l "drop_norev" Sb-*.log | sed 's/^Sb-//;s/\.log$//' > crashed.lst
{ for c in $(cat crashed.lst); do grep "^$c " coins.lst; done
  for c in CLAUDIA FONE PAID; do echo "$c /data/tk0393/tk401/mid-sol/$c.csv.gz sol"; done; } > redo.lst
one() { $PY $S/tk409-ev-conv.py $1 $2 $B/Sb-$1 1000 b > $B/Sb-$1.log 2>&1; }
export -f one
xargs -P 15 -L1 bash -c 'one $0 $1' < redo.lst
touch $B/sbfix.done
