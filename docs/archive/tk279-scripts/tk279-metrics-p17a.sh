#!/usr/bin/env bash
# TK-279: метрики p17a (Г-98/Г-101, 117 хешей) из closes psim; сверка verify_metrics независимым разбором.
D=/data/tk0279/metrics-p17a; cd /opt/alpha-launch/tools/launch || exit 9
S=/data/tk0279/p17a/psim/closes-p14f1-cap0-{m}.json
python3 build_metrics.py --routes $D/routes-p17a.json --out $D/metrics-p17a.jsonl $S > $D/build.out 2>&1 || exit 1
python3 verify_metrics.py --metrics $D/metrics-p17a.jsonl --routes $D/routes-p17a.json --n 12 $S > $D/verify.out 2>&1 || exit 2
echo ok > $D/done
