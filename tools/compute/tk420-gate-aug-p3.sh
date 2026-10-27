#!/bin/bash
# TK-420 вар.3–5, B (Gate), август, шаг 3: поток сделок Gate (авг) -> признаки на событиях детектора (tk420-flow2-aug.py, ex=gate).
# Выход: /data/tk0393/tk420aug/gate/flow/flowgate2-aug-{events,legs}.csv; итог — gate/flow/p3.rc.
#   alsched submit --kind net --name tk420-gate-aug-p3 --max-runtime 2h --est 30m --cwd /data/tk0393/tk420aug -- bash /data/tk0393/tk420aug/tk420-gate-aug-p3.sh
cd /data/tk0393/tk420aug/gate || exit 1
mkdir -p flow; rm -f flow/p3.rc
python3 ../tk420-flow2-aug.py /data/tk0393/tk420aug/gate/candles pump-dump-scenarios-legs-aug-gate.csv /data/tk0393/tk411/gate-list.tsv pump-dump-episodes-aug-gate-2026-10-27.csv flow/flowgate2-aug 4 gate > flow/flowgate2-aug.log 2>&1
echo $? > flow/p3.rc
