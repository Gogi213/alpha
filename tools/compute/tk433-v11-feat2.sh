#!/bin/bash
# TK-433 v11 дельта (Судья 07:50): август с OI/фандингом из tk425/aug-oi; re-merge сент. и авг. с sf_diff = spot_gap_bps_leg. Метка v11g.done, код v11g.rc.
cd /data/tk0433/v11f || exit 9
PY=/data/tk421/venv/bin/python3
rm -f ../v11g.done ../v11g.rc
mkdir -p g-2026-08 g-2026-09
$PY tk422-features.py f-2026-08/windows.csv g-2026-08/tk422-features.csv --procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --tk415 /srv/alpha-sda/tk425/aug-oi --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv > g-2026-08/feat.log 2> g-2026-08/feat.err || { echo 2 > ../v11g.rc; exit 2; }
$PY tk433-features.py merge f-2026-08/windows.csv f-2026-08/own.csv g-2026-08/tk422-features.csv g-2026-08/features-2026-08.csv > g-2026-08/merge.log 2>&1 || { echo 3 > ../v11g.rc; exit 3; }
cp f-2026-09/windows.csv f-2026-09/own.csv f-2026-09/tk422-features.csv g-2026-09/ 
$PY tk433-features.py merge f-2026-09/windows.csv f-2026-09/own.csv f-2026-09/tk422-features.csv g-2026-09/features-2026-09.csv > g-2026-09/merge.log 2>&1 || { echo 4 > ../v11g.rc; exit 4; }
echo 0 > ../v11g.rc
touch ../v11g.done
