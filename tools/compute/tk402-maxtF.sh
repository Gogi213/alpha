#!/usr/bin/env bash
# max-T по A=1/5/30 с с исправленным фандингом (FGFIX); выход maxtF<A>.*
cd /data/tk0402
rm -f maxtF.done maxtF.failed
for a in 1000 5000 30000; do
  OPENBLAS_NUM_THREADS=15 OMP_NUM_THREADS=15 /data/tk0380/npvenv/bin/python tk402-maxt.py s2a$a gate-cover.tsv /data/tk0402/maxtF$a 2000 > maxtF$a.log 2>&1 || { touch maxtF.failed; exit 1; }
done
touch maxtF.done
