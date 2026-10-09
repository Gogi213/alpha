#!/usr/bin/env bash
# TK-115: хвост дельта-волны вторым заданием — те же сутки с конца (sep→feb), заявка суток в tk115-delta-day.sh не даёт посчитать дважды.
#   env: D=2 TP=3; по концу обоих заданий (wave.done первого + все сутки done) ставит wave-all.done.
set -uo pipefail
D=${D:-2}; export TP=${TP:-3}
for m in sep aug jul jun may apr mar feb; do awk -F, -v m="$m" 'NR>1 && $1==m{print $1, $2}' /data/tk0115/delta/b1-symdays.csv | sort -u | tac; done \
  | xargs -P "$D" -L1 bash -c 'bash /data/tk0115/tk115-delta-day.sh "wave/$1" "$0" "$1"'
while [ ! -e /data/tk0115/delta/wave.done ]; do sleep 30; done
touch /data/tk0115/delta/wave-all.done
