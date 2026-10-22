#!/bin/bash
# TK-268 ш.1: метрики для строк Летописи без метрик — из готовых closes cap 0, без бэктеста.
# Выход /data/tk0268/{metrics-A,metrics-B,metrics-final}.jsonl, verify-{A,B}.txt, cover.txt. A: порядок источников S1 S2 S3, B: S2 S1 S3 (хеш из нескольких прогонов
# берёт тот, у кого Σ$ сходится с каноном); месяцы янв–окт (канон R1 TK-182 доходит до 2026-10-02).
set -e
mkdir -p /data/tk0268 && cd /data/tk0268/launch
S1=/data/tk0231/psim/closes-p14f1-cap0-{m}.json
S2=/data/tk0196/psim/closes-f1-cap0-{m}.json
S3=/data/p12r3a/closes-r2bd-cap0-{m}.json
D1=/data/tk0251/ps/closes-cap0-{mon}.json
D2=/data/tk0251/p3/ps/closes-cap0-{mon}.json
D3=/data/tk0113/drop2/r1/closes-cap0-{mon}.json
D4=/data/tk0113/drop2/ext/closes-cap0-{m}.json
AL="--alias-cells /data/launch/registry/cells.jsonl"
MO=2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09,2026-10
DIR="--direct $D1 --direct $D2 --direct $D3 --direct $D4"
python3 build_metrics.py --out /data/tk0268/metrics-A.jsonl --months $MO $DIR $AL $S1 $S2 $S3
python3 build_metrics.py --out /data/tk0268/metrics-B.jsonl --months $MO $DIR $AL $S2 $S1 $S3
for v in A B; do python3 verify_metrics.py --metrics /data/tk0268/metrics-$v.jsonl --n 14 $DIR $AL $S1 $S2 $S3 | tee /data/tk0268/verify-$v.txt | tail -1; done
python3 -I /data/tk0268/cover.py | tee /data/tk0268/cover.txt
