#!/usr/bin/env bash
# TK-120: лоты ленты/отмен за окно TL с до входа по сделкам B1 (все сутки b1-symdays.csv янв–сен), маркер /data/p12r3a/w<TL>.done
#   tk120-thr-w-wave.sh <TL>   env: D=4 GT=2
set -uo pipefail
export TL=${1:?TL}; D=${D:-4}; export GT=${GT:-2}
awk -F, 'NR>1{print $1, $2}' /data/tk0115/delta/b1-symdays.csv | sort -u \
  | xargs -P "$D" -L1 bash -c 'bash /data/tk0115/tk120-thr-w-day.sh "$0" "$1"'
touch /data/p12r3a/w$TL.done
