#!/usr/bin/env bash
# TK-064: R1 по пулу — месяц за месяцем, каждый месяц отдельным замком benchrun stand (между волнами других).
set -uo pipefail
V=/data/tk044/final3/verdict.csv
for pair in 01:jan 02:feb 03:mar 04:apr 05:may 06:jun 07:jul 08:aug 09:sep 10:oct; do
  n=${pair%%:*}; m=${pair##*:}
  days=$(awk -F, -v p="2026-$n-" 'NR>1 && $17=="пускаем" && index($2,p)==1{print $2}' $V | sort -u | tr '\n' ' ')
  [ -n "$days" ] || continue
  echo "$(date +%F\ %T) start $m" >> /data/tk064/pool-chain.log
  /data/benchrun.sh stand bash /data/tk064/tk064-pool.sh m-$m $m $days >> /data/tk064/pool-chain.log 2>&1
  echo "$(date +%F\ %T) done $m rc=$?" >> /data/tk064/pool-chain.log
done
