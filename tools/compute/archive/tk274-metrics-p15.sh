#!/usr/bin/env bash
# TK-274: метрики F1 П-15 (cells+monthly маршруты) из closes psim L-P15F1; сверка verify_metrics независимым разбором. Задание alsched gate 1 ядро.
D=/data/tk0274/metrics; cd /opt/alpha-launch/tools/launch || exit 9
S=/data/launch/out/L-P15F1/psim/closes-p14f1-cap0-{m}.json
N=$(python3 -c "import json;print(sum(1 for v in json.load(open('$D/routes-p15.json'))['routes'].values() if v in ('cells','monthly')))")
python3 build_metrics.py --routes $D/routes-p15.json --out $D/metrics-p15.jsonl $S > $D/build.out 2>&1 || exit 1
python3 verify_metrics.py --metrics $D/metrics-p15.jsonl --routes $D/routes-p15.json --n $N $S > $D/verify.out 2>&1 || exit 2
echo ok > $D/done
