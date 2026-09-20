#!/usr/bin/env bash
# Ёмкость по подходам (F2 этапа F, dev-plan-2026-09-20.md §3): замер доли
# лестницы 0,02–0,2 % над стеной, исполненной при постановке «на подходе»
# (`arm_ms`) и жизни до снятия взвода (`disarm_ms`).
#   bin/approach-capacity.sh [D …]        (по умолчанию 10 20 30)
# Сырьё — study/approaches/D<D>/ (прогон bin/approach-scan.sh); артефакты —
# study/capacity-approach/D<D>/<набор>/capacity-<SYMBOL>.csv (читатели:
# tools/compute/leg-distance.py, leg-clear.py, fill-capacity.py).
set -euo pipefail
cd /opt/alpha-compute
DS=("$@")
[ ${#DS[@]} -gt 0 ] || DS=(10 20 30)
for d in "${DS[@]}"; do
  src="study/approaches/D$d"
  if [ ! -d "$src" ]; then
    echo "D=$d: нет $src — пропуск" >&2
    continue
  fi
  out="study/capacity-approach/D$d"
  echo "== $(date -u +%FT%TZ) capacity targets=approaches D=$d → $out"
  bin/alpha lob fill-capacity --root root --touches-from "$src" --targets approaches \
    --h3-mode notional --h3-usd 10000 --band-bps 70 --pre-secs 1,60,300 \
    --post-secs 10,60,300,1800 \
    --set a45-bid:age=2700,side=bid --set a15-bid:age=900,side=bid \
    --set a45-ask:age=2700,side=ask \
    --out-dir "$out" > "study/capacity-approach-D$d.log" 2>&1
  echo "== $(date -u +%FT%TZ) D=$d готов: $(ls "$out"/*/capacity-*.csv 2>/dev/null | wc -l) файлов"
done
