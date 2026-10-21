#!/bin/bash
# TK-272: сито F2 Г-10 / Г-112 (§11.3–11.4) — closes с монетой для f2d и g10-112, затем сито F1∪F2 как tk272-sieve.sh (без движка, В-213).
#   tk272-sieve2.sh  (на calc; дерево tk272 — /data/tk0272/sieve2/tree = git archive головы, docs/registry — копия canon calc без колонки sieve.note)
# -> /data/tk0272/sieve2/{km.json,cov.csv,sieve.csv,plateau.csv,rc.txt}; closes-sym — <RUN>/psim/closes-sym-p14f1-cap0-<мес>.json (два запуска параллельно, по 3 месяца).
D=/data/tk0272/sieve2; T=$D/tree/tools; PY=/data/tk0278/venv/bin/python; cd $D || exit 2; rm -f rc.txt
{ { PM=3 bash /data/tk0272/tk272-f2-symclose.sh /data/tk0272/f2d & PM=3 bash /data/tk0272/tk272-f2-symclose.sh /data/tk0272/g10-112 & wait; } \
  && [ -e /data/tk0272/f2d/psim/sym.done ] && [ -e /data/tk0272/g10-112/psim/sym.done ] \
  && $PY $T/compute/tk272-sieve-prep.py km.json cov.csv Г-10,Г-112 \
  && cp /data/tk0278/sieve/cellhash-map-2026-10-11.csv . \
  && EXTRA_SYM=/data/tk0272/f2d/psim/closes-sym-p14f1-cap0-{mo}.json:/data/tk0272/g10-112/psim/closes-sym-p14f1-cap0-{mo}.json $PY $T/compute/tk278-sieve.py km.json cov.csv cellhash-map-2026-10-11.csv sieve.csv \
  && $PY $T/compute/tk237-plateau.py sieve.csv cov.csv plateau.csv; } > sieve.log 2>&1
echo $? > rc.txt
