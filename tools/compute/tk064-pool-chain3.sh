#!/usr/bin/env bash
# TK-064: R1 по пулу, февраль→октябрь v2: сутки параллельно D=3×TP=4, CPUQuota 1200 %; январь доделывает tk064-pool-chain.
set -uo pipefail
V=/data/tk044/final3/verdict.csv; L=/data/tk064/pool-chain.log
until grep -q "start feb" $L; do sleep 3; done
systemctl stop tk064-pool-chain; sleep 3
pkill -f "tk064-pool.sh m-feb" ; sleep 2
rm -rf /data/tk064/pool/m-feb
for pair in 02:feb 03:mar 04:apr 05:may 06:jun 07:jul 08:aug 09:sep 10:oct; do
  n=${pair%%:*}; m=${pair##*:}
  days=$(awk -F, -v p="2026-$n-" 'NR>1 && $17=="пускаем" && index($2,p)==1{print $2}' $V | sort -u | tr '\n' ' ')
  [ -n "$days" ] || continue
  echo "$(date +%F\ %T) start $m (bare)" >> $L
  D=3 TP=4 GT=2 bash /data/tk064/tk064-pool2.sh m-$m $m $days >> $L 2>&1
  echo "$(date +%F\ %T) done $m rc=$?" >> $L
done
