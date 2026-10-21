#!/usr/bin/env bash
# TK-279: метрики Г-81 (72 хеша) из closes psim; сверка verify_metrics независимым разбором.
D=/data/tk0279/metrics-g81; cd /opt/alpha-launch/tools/launch || exit 9
S=/data/tk0279/p17g81/psim/closes-p14f1-cap0-{m}.json
python3 build_metrics.py --routes $D/routes-g81.json --out $D/metrics-g81.jsonl $S > $D/build.out 2>&1 || exit 1
python3 verify_metrics.py --metrics $D/metrics-g81.jsonl --routes $D/routes-g81.json --n 12 $S > $D/verify.out 2>&1 || exit 2
echo ok > $D/done
