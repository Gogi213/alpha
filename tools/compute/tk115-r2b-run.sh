#!/usr/bin/env bash
# TK-115 R2-B: все B1-символо-сутки янв–сен одним проходом, D суток вразброс; маркер /data/p12r3a/r2b.done.
#   tk115-r2b-run.sh [мес...]   env: D=3 TP=4 GT=2 THR=/data/p12r3a/thr5 BIN=/data/tk0115/bin/alpha-tk115f
# Предусловие (отказ до счёта): в e-thresholds-<ГГГГ-ММ>.csv каждого месяца есть все ключи 24 порогов Q (e112/e116 × W15/30/60 × q33/50/67/80) и W_ms_median.
set -uo pipefail
D=${D:-3}; export TP=${TP:-4}; THR=${THR:-/data/p12r3a/thr5}; export THR
mons=("$@"); [ ${#mons[@]} -gt 0 ] || mons=(jan feb mar apr may jun jul aug sep)
for m in "${mons[@]}"; do
  n=$(printf '%s\n' jan feb mar apr may jun jul aug sep | grep -nx "$m" | cut -d: -f1); f=$THR/e-thresholds-2026-$(printf %02d "$n").csv
  for fam in e112 e116; do for w in 15 30 60; do for q in 33 50 67 80; do
    grep -q "^${fam}_W${w}_q${q}," "$f" || { [ "$w" = 30 ] && grep -q "^${fam}_q${q}," "$f"; } || { echo "НЕТ ПОРОГА $fam W$w q$q в $f" >&2; exit 4; }
  done; done; done
  grep -q '^W_ms_median,' "$f" || { echo "НЕТ W_ms_median в $f" >&2; exit 4; }
done
for m in "${mons[@]}"; do awk -F, -v m="$m" 'NR>1 && $1==m{print $1, $2}' /data/tk0115/delta/b1-symdays.csv | sort -u; done \
  | xargs -P "$D" -L1 bash -c 'bash /data/tk0115/tk115-r2b-day.sh "$1" "$0" "$1"' 2>&1
[ -s /data/p12r3a/r2b/fail.txt ] || true
touch /data/p12r3a/r2b.done
