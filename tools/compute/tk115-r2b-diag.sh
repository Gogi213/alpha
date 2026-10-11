#!/usr/bin/env bash
# TK-115 R2-B: диагностика GRID_FAIL (wall2 solo) по одному символу-суткам под ALPHA_DIAG; выход /data/p12r3a/r2b-diag/<метка>/log.
set -uo pipefail
B1=/data/tk0115/delta/b1-symdays.csv; X=/data/tk0115/delta/b1-diag.csv
export ALPHA_DIAG=1 O=/data/p12r3a/r2b-diag
for p in "ada aug 2026-08-31 ADAUSDT" "gram aug 2026-08-31 GRAMUSDT" "stx sep 2026-09-14 STXUSDT"; do set -- $p
  awk -F, -v d="$3" -v s="$4" 'NR==1||($2==d&&$3==s)' "$B1" > "$X"
  B1=$X bash /data/tk0115/tk115-r2b-day.sh "$1" "$2" "$3"
done
