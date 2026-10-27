#!/bin/bash
# TK-421 возврат Судьи 27.10: артефактные минуты → шаг A → B1 → B2a → B2b одним проходом; rc — /data/tk421/v7.rc
set -e
cd /data/tk421
PY=/data/tk421/venv/bin/python3
R=/srv/alpha-sda/candles-1m
$PY tk421-art.py $R art-probe.json 4 40 2026-09 > art-probe.log 2>&1
$PY tk421-art.py $R art-2026-09.json 15 0 2026-09 > art.log 2>&1
$PY tk421-moves.py $R moves2 15 0 2026-09 art-2026-09.json > moves2.log 2>&1
$PY tk421-b1-thresh.py moves2 b1v2-2026-09.json 2026-09 1000 > b1v2.log 2>&1
$PY tk421-events.py $R b1v2-2026-09.json b2probe2 4 20 2026-09 art-2026-09.json > b2probe2.log 2>&1
$PY tk421-events.py $R b1v2-2026-09.json b2v2 15 0 2026-09 art-2026-09.json > b2v2.log 2>&1
$PY tk421-b2b.py $R b1v2-2026-09.json b2v2 b2bv2 2026-09 art-2026-09.json > b2bv2.log 2>&1
echo 0 > v7.rc
