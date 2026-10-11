#!/usr/bin/env bash
# TK-064: ретрай упавших touches по месяцам — как только месяц закончен в pool-chain.log (голый юнит, CPUQuota 300 %).
set -uo pipefail
L=/data/tk064/pool-chain.log
for m in feb mar apr may jun jul aug sep oct; do
  until grep -q "done $m rc" $L; do sleep 60; done
  F=/data/tk064/pool/m-$m/fail.txt
  days=$(awk '$3=="TOUCHES_FAIL"{print $1}' $F | sort -u | tr '\n' ' ')
  [ -n "$days" ] || { echo "$(date +%F\ %T) retry $m: нет упавших" >> $L; continue; }
  echo "$(date +%F\ %T) retry start $m ($(grep -c TOUCHES_FAIL $F) монето-суток)" >> $L
  D=1 TP=3 GT=2 bash /data/tk064/tk064-retry.sh m-$m $m $days >> $L 2>&1
  echo "$(date +%F\ %T) retry done $m rc=$? fail-retry=$(grep -c . /data/tk064/pool/m-$m/fail-retry.txt)" >> $L
done
