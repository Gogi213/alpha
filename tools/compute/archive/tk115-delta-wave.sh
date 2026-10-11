#!/usr/bin/env bash
# TK-115 дельта-волна фев–сен: сутки из b1-symdays.csv, по сутки — tk115-delta-day.sh (метка wave/<сутки>), D суток вразброс.
#   tk115-delta-wave.sh [мес...]   env: D=3 TP=4 (потоков touches на сутки)  → /data/tk0115/delta/wave/<сутки>/, маркер wave.done
set -uo pipefail
D=${D:-3}; export TP=${TP:-4}
mons=("$@"); [ ${#mons[@]} -gt 0 ] || mons=(feb mar apr may jun jul aug sep)
for m in "${mons[@]}"; do awk -F, -v m="$m" 'NR>1 && $1==m{print $1, $2}' /data/tk0115/delta/b1-symdays.csv | sort -u; done \
  | xargs -P "$D" -L1 bash -c 'bash /data/tk0115/tk115-delta-day.sh "wave/$1" "$0" "$1"'
touch /data/tk0115/delta/wave.done
