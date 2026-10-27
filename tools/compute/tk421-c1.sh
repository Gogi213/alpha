#!/bin/bash
# TK-421: контроли C1 (протокол v2 TK-426 §4) на событиях v7c (b2bv2); rc — /data/tk421/c1.rc
cd /data/tk421
mkdir -p c1
/data/tk421/venv/bin/python3 /data/tk426/tk426-controls.py b2bv2/pump-events-2026-09.csv /srv/alpha-sda/candles-1m c1/c1-windows-2026-09.csv 2026-09 --diag c1/c1-diag-2026-09.json > c1/c1.log 2>&1
echo $? > c1.rc
