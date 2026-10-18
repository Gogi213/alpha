#!/bin/bash
# TK-268 ш.7: метрики для строк без closes — R2 волна A (p12r2a: va/van/vand) и R2 (p12r2: v/vn/vt/vtn) как источники, текущие routes. Без бэктеста.
# G = янв–сен, H = янв–окт; выход /data/tk0268/metrics-{G,H}.jsonl; затем cover4.py (final3 ∪ G ∪ H, тот же check против канона) -> metrics-final4.jsonl.
set -e
cd /data/tk0268/launch
SRC=""
for d in /data/p12r2a:va /data/p12r2a:van /data/p12r2a:vand /data/p12r2:v /data/p12r2:vn /data/p12r2:vt /data/p12r2:vtn; do SRC="$SRC ${d%%:*}/closes-${d##*:}-cap0-{m}.json"; done
M9=2026-01,2026-02,2026-03,2026-04,2026-05,2026-06,2026-07,2026-08,2026-09
for v in "G $M9" "H $M9,2026-10"; do set -- $v
  python3 build_metrics.py --out /data/tk0268/metrics-$1.jsonl --zero-fill --routes /opt/alpha-launch/tools/launch/routes.json --months $2 $SRC
done
python3 -I /data/tk0268/cover4.py | tee /data/tk0268/cover4.txt
