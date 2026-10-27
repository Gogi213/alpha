#!/bin/bash
# TK-421 шаг B2a: проба 40 инстр./рынок (4 процесса), затем полный сентябрь 15 процессов; rc — в /data/tk421/b2.rc
set -e
cd /data/tk421
PY=/data/tk421/venv/bin/python3
$PY tk421-events.py /srv/alpha-sda/candles-1m b1-thresh-2026-09.json b2probe 4 40 2026-09 > b2probe.log 2>&1
$PY tk421-events.py /srv/alpha-sda/candles-1m b1-thresh-2026-09.json b2 15 0 2026-09 > b2.log 2>&1
echo 0 > b2.rc
