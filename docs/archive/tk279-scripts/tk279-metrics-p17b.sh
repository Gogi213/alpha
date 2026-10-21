#!/usr/bin/env bash
# TK-279: метрики p17b (Г-135/Г-136/Г-137, 103 хеша) из closes psim; сверка verify_metrics независимым разбором.
D=/data/tk0279/metrics-p17b; cd /opt/alpha-launch/tools/launch || exit 9
S=/data/tk0279/p17b/psim/closes-p14f1-cap0-{m}.json
python3 build_metrics.py --routes $D/routes-p17b.json --out $D/metrics-p17b.jsonl $S > $D/build.out 2>&1 || exit 1
python3 verify_metrics.py --metrics $D/metrics-p17b.jsonl --routes $D/routes-p17b.json --n 12 $S > $D/verify.out 2>&1 || exit 2
echo ok > $D/done
