#!/bin/bash
# TK-420 вар.5, B (Gate), август: марк-аут TK-419 (tk419-markout.py + tk419-markout-sum.py B). Выход /data/tk0393/tk420aug/gate/mo/. Итог — gate/mo/p5.rc.
#   alsched submit --kind prod --name tk420-gate-aug-p5 --max-runtime 1h --est 10m --cwd /data/tk0393/tk420aug -- bash /data/tk0393/tk420aug/tk420-gate-aug-p5.sh
cd /data/tk0393/tk420aug || exit 1
mkdir -p gate/mo; rm -f gate/mo/p5.rc
python3 tk419-markout.py gate/obgate/obfeat-events.csv gate/flow/flowgate2-aug-legs.csv gate/pump-dump-scenarios-legs-aug-gate.csv /data/tk0393/tk420aug/gate/candles futures_usdt gate/mo/markout-B-aug.csv 15 > gate/mo/markout.log 2>&1 && \
/data/tk0278/venv/bin/python -W ignore tk419-markout-sum.py gate/mo/markout-B-aug.csv B gate/mo/markout-B-aug-sum.csv > gate/mo/markout-B-aug-sum.md 2> gate/mo/markout-B-aug-sum.err
echo $? > gate/mo/p5.rc
