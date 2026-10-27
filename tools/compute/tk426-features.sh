#!/bin/bash
# TK-426: признаки TK-422 v5 (без стакана) на событиях + контролях C1/C2, три чистых рынка; rc — /data/tk0426/feat.rc
cd /data/tk0426
rm -f feat.rc
python3 tk422-features.py tk426-windows.csv tk426-features-2026-09.csv --procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv > feat.log 2> feat.err
echo $? > feat.rc
