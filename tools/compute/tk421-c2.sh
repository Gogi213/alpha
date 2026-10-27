#!/bin/bash
# TK-421: контроли C2 на событиях v7c; rc — /data/tk421/c2.rc
cd /data/tk421
mkdir -p c2
/data/tk421/venv/bin/python3 tk421-c2.py /srv/alpha-sda/candles-1m b1v2-2026-09.json b2bv2/pump-events-2026-09.csv c2/c2-windows-2026-09.csv 2026-09 art-2026-09.json --per 3 --procs 15 --diag c2/c2-diag-2026-09.json > c2/c2.log 2>&1
echo $? > c2.rc
