#!/bin/bash
# TK-433 v11, сентябрь: окна (X=5 → гистограмма) → X_D=q99 → события по q99 → меры волатильности по (D, знак). Метка v11.done, код возврата v11.rc.
cd /data/tk0433/v11 || exit 9
PY=/data/tk421/venv/bin/python3
R=/srv/alpha-sda/candles-1m
L4=/data/tk421/l4v8/l4v8-2026-09.json
ART=/data/tk421/art-2026-09.json
rm -f ../v11.done ../v11.rc
set -e
$PY tk435-windows.py events $R $L4 $ART h --procs 15 --x 5 > h.log 2>&1
$PY tk435-windows.py quant h/hist-2026-09.json xq.json > quant.log 2>&1
$PY tk435-windows.py events $R $L4 $ART ev --procs 15 --x q99 --xq xq.json > ev.log 2>&1
for D in 90 180 360 720 1440; do
  for S in + -; do
    X=$($PY -c "import json;print(json.load(open('xq.json'))['$D|$S']['q99'])")
    N=$([ "$S" = "+" ] && echo p || echo m)
    $PY tk435-vol.py ev/windows-2026-09.csv $D $X vol-D$D$N.csv $R /srv/alpha-sda/candles-1m-aug --procs 15 > vol-D$D$N.log 2>&1
  done
done
echo 0 > ../v11.rc
touch ../v11.done
