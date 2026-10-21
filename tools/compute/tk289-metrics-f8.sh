#!/usr/bin/env bash
# TK-289: метрики F1 П-18 ось 8 (8 хешей) из closes psim; сверка verify_metrics независимым разбором. Как tk289-metrics-f6v.sh.
D=/data/tk0289/metrics-f8; cd /opt/alpha-launch/tools/launch || exit 9
S=/data/tk0289/f8/psim/closes-p14f1-cap0-{m}.json
rm -f $D/done
python3 build_metrics.py --routes $D/routes-p18.json --out $D/metrics-p18.jsonl $S > $D/build.out 2>&1 || exit 1
python3 verify_metrics.py --metrics $D/metrics-p18.jsonl --routes $D/routes-p18.json --n 12 $S > $D/verify.out 2>&1 || exit 2
echo ok > $D/done
