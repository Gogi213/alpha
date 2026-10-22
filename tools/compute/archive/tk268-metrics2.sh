#!/bin/bash
# TK-268 ш.5: метрики для оставшихся строк Летописи — доп. источники closes (R2/R3 p12r3a, cs tk0113, TK-274 F1 и g134), текущие routes. Без бэктеста.
# Выход /data/tk0268/metrics-C.jsonl; затем cover2.py (прежние ∪ A/B/C, тот же check против канона) -> metrics-final2.jsonl.
set -e
cd /data/tk0268/launch
P=/data/p12r3a
SRC="/data/tk0231/psim/closes-p14f1-cap0-{m}.json /data/tk0196/psim/closes-f1-cap0-{m}.json $P/closes-r2bd-cap0-{m}.json $P/closes-r3-cap0-{m}.json $P/closes-r3d-cap0-{m}.json $P/closes-r3n-cap0-{m}.json $P/closes-r3nd-cap0-{m}.json /data/tk0113/cs-cap0-{m}.json /data/launch/out/L-P15F1/psim/closes-p14f1-cap0-{m}.json"
DIR="--direct /data/tk0251/ps/closes-cap0-{mon}.json --direct /data/tk0251/p3/ps/closes-cap0-{mon}.json --direct /data/tk0113/drop2/r1/closes-cap0-{mon}.json --direct /data/tk0113/drop2/ext/closes-cap0-{m}.json --direct /data/tk0274/g134-psim/ps/closes-cap0-{mon}.json"
MO=2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09,2026-10
python3 build_metrics.py --out /data/tk0268/metrics-C.jsonl --routes /opt/alpha-launch/tools/launch/routes.json --months $MO $DIR --alias-cells /data/launch/registry/cells.jsonl $SRC
python3 -I /data/tk0268/cover2.py | tee /data/tk0268/cover2.txt
