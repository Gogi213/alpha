#!/usr/bin/env bash
# TK-402 max-T по объединённому набору FF (s2a) + FS (s2s), фандинг исправлен (FGFIX), пошаговый; A=1 с — B=10000 (вердикт), A=5/30 с — B=2000 (+SE q95 в json)
# Выход: /data/tk0402/tk402-maxtG-<A>-2026-10-26.{csv,json}, -days.csv; метка maxtG.done
cd /data/tk0402
rm -f maxtG.done maxtG.failed
for ab in 1000:10000 5000:2000 30000:2000; do
  a=${ab%%:*}; B=${ab##*:}
  OPENBLAS_NUM_THREADS=15 OMP_NUM_THREADS=15 /data/tk0380/npvenv/bin/python tk402-maxt.py s2a$a,s2s$a gate-cover.tsv /data/tk0402/tk402-maxtG-$a-2026-10-26 $B > maxtG$a.log 2>&1 || { touch maxtG.failed; exit 1; }
done
touch maxtG.done
