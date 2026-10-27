#!/bin/bash
cd /data/tk421
PY=/data/tk421/venv/bin/python3
$PY tk421-l4v8.py /srv/alpha-sda/candles-1m b2bv2/pump-events-2026-09.csv l4v8 2026-09 art-2026-09.json 15 1000 > l4v8.log 2>&1
echo $? > l4v8.rc
