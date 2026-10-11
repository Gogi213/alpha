#!/usr/bin/env bash
# TK-064 шаг 3: независимый пересчёт колонок R1 (tk025-recompute.py) против approaches флага вкл. на 2 сутках; под benchrun.sh stand.
#   tk064-recompute.sh <каталог гейта g1> ; результат — <g1>/recompute-<SYM>.json и .log
set -uo pipefail
G=$1; PY=/data/tk064/tk025-recompute.py
for c in SUIUSDT-2026-09-20 AAVEUSDT-2026-09-20; do s=${c%%-*}
  python3 "$PY" "/data/alpha/epochs/e-sep/root/$c.binlog" "$G/$c/on/approaches-$s.csv" --approach-bps 20 --day 2026-09-20 --json "$G/recompute-$s.json" > "$G/recompute-$s.log" 2>&1; echo "$s rc=$?"
done
