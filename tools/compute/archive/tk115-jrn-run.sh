#!/usr/bin/env bash
# TK-115 журнал на исполнении, все B1-символо-сутки янв–сен (3 533), D суток вразброс; маркер /data/p12r3a/jrn.done.
#   tk115-jrn-run.sh [мес...]   env: D=3 TP=4 GT=2 BIN=/data/tk0115/bin/alpha-tk115f
set -uo pipefail
D=${D:-3}; export TP=${TP:-4}
mons=("$@"); [ ${#mons[@]} -gt 0 ] || mons=(jan feb mar apr may jun jul aug sep)
for m in "${mons[@]}"; do awk -F, -v m="$m" 'NR>1 && $1==m{print $1, $2}' /data/tk0115/delta/b1-symdays.csv | sort -u; done \
  | xargs -P "$D" -L1 bash -c 'bash /data/tk0115/tk115-jrn-day.sh "$1" "$0" "$1"' 2>&1
touch /data/p12r3a/jrn.done
