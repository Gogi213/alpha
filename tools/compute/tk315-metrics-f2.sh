#!/usr/bin/env bash
# TK-315: метрики П-17 F2 (383 хеша) из closes psim; сверка verify_metrics независимым разбором. Как tk279-metrics-p17b.sh.
D=/data/tk0315/metrics-f2; cd /opt/alpha-launch/tools/launch || exit 9
S=/data/tk0315/p17f2/psim/closes-p14f1-cap0-{m}.json
rm -f $D/done
python3 build_metrics.py --routes $D/routes-p17f2.json --out $D/metrics-p17f2.jsonl $S > $D/build.out 2>&1 || exit 1
python3 verify_metrics.py --metrics $D/metrics-p17f2.jsonl --routes $D/routes-p17f2.json --n 12 $S > $D/verify.out 2>&1 || exit 2
echo ok > $D/done
