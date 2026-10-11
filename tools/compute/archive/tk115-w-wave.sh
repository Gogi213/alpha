#!/usr/bin/env bash
# TK-115 замер W: все сутки b1-symdays.csv (янв–сен), пробные сутки (мар 03-07) первыми.  env: D=6 GT=2 → маркер /data/tk0115/delta/w.done
set -uo pipefail
D=${D:-6}; export GT=${GT:-2}
{ echo "mar 2026-03-07"; awk -F, 'NR>1{print $1, $2}' /data/tk0115/delta/b1-symdays.csv | sort -u; } \
  | xargs -P "$D" -L1 bash -c 'bash /data/tk0115/tk115-w-day.sh "$0" "$1"'
touch /data/tk0115/delta/w.done
