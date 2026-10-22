#!/bin/bash
# TK-272: сито F2 для 7 полных гипотез — python над готовыми closes с монетой (без движка, В-213).
#   tk272-sieve.sh  (на calc; дерево tk272 в /data/tk0272/sieve/tree, docs/registry -> /data/launch/registry)
# -> /data/tk0272/sieve/{km.json,cov.csv,sieve.csv,plateau.csv,rc.txt}. Ключи F2 — closes run2 (tk272-f2-symclose.sh) через EXTRA_SYM.
D=/data/tk0272/sieve; T=$D/tree/tools; PY=/data/tk0278/venv/bin/python; cd $D || exit 2; rm -f rc.txt
{ $PY $T/compute/tk272-sieve-prep.py km.json cov.csv \
  && cp /data/tk0278/sieve/cellhash-map-2026-10-11.csv . \
  && EXTRA_SYM=/data/tk0272/run2/psim/closes-sym-p14f1-cap0-{mo}.json $PY $T/compute/tk278-sieve.py km.json cov.csv cellhash-map-2026-10-11.csv sieve.csv \
  && $PY $T/compute/tk237-plateau.py sieve.csv cov.csv plateau.csv; } > sieve.log 2>&1
echo $? > rc.txt
