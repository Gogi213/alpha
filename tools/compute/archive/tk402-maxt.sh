#!/usr/bin/env bash
cd /data/tk0402
OPENBLAS_NUM_THREADS=15 OMP_NUM_THREADS=15 /data/tk0380/npvenv/bin/python tk402-maxt.py s2 gate-cover.tsv /data/tk0402/maxt 2000 > maxt.log 2>&1 && touch maxt.done
