#!/bin/bash
# TK-272: метрики 121 хеша F2d из готовых closes cap 0 (psim f2d), без бэктеста; только эти маршруты (routes-f2d.json)
set -e
mkdir -p /data/tk0272/metrics-f2d
cp /data/tk0272/metrics/{build_metrics.py,metrics.py,verify_metrics.py} /data/tk0272/metrics-f2d/
cd /data/tk0272/metrics-f2d
S=/data/tk0272/f2d/psim/closes-p14f1-cap0-{m}.json
MO=2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09
python3 build_metrics.py --out /data/tk0272/metrics-f2d.jsonl --months $MO --routes routes-f2d.json $S
python3 verify_metrics.py --metrics /data/tk0272/metrics-f2d.jsonl --n 14 --routes routes-f2d.json $S | tee verify.txt | tail -3
touch done
