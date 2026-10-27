#!/bin/bash
# TK-420 вар.3–5, B (Gate), август, шаг 4: минутные срезы книги Gate авг (часы событий) -> признаки книги на событиях (как tk414-obgate-run.sh).
# Выход: /data/tk0393/tk420aug/gate/obgate/{need.tsv,min/,obfeat-events.csv}; итог — gate/obgate/p4.rc.
#   alsched submit --kind prod --name tk420-gate-aug-p4 --max-runtime 2h --est 30m --cwd /data/tk0393/tk420aug -- bash /data/tk0393/tk420aug/tk420-gate-aug-p4.sh
cd /data/tk0393/tk420aug/gate || exit 1
mkdir -p obgate; rm -f obgate/p4.rc
T=/data/tk0393/tk420aug
python3 $T/tk420-obgate-need-aug.py obgate/need.tsv > obgate/need.log 2>&1 && \
python3 $T/tk414-obmin-gate.py obgate/need.tsv obgate/min 15 > obgate/obmin.log 2>&1 && \
python3 $T/tk414-obfeat.py flow/flowgate2-aug-events.csv obgate/min obgate/obfeat-events.csv > obgate/obfeat.log 2>&1
echo $? > obgate/p4.rc
