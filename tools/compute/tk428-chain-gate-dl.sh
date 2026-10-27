#!/bin/bash
# TK-428 п.3 (G-017): Gate архив сделок 202601–202606 (futures_usdt/trades + spot/deals) по месяцам, PAR 16 (бан 23.10 при 32).
# Сырьё не копится: месяц m+2 стартует после build.done месяца m (сборка tk428-chain-gate-build.sh удаляет сырьё).
# Использование: tk428-chain-gate-dl.sh   (ROOT=/srv/alpha-sda/tk428; LIST=/data/tk0393/tk423/gate-list.tsv)
set -u
root=${ROOT:-/srv/alpha-sda/tk428}; list=${LIST:-/data/tk0393/tk423/gate-list.tsv}; here=$(dirname "$(readlink -f "$0")")
prev2=""; prev1=""
for m in 202601 202602 202603 202604 202605 202606; do
  [ -n "$prev2" ] && until [ -e "$root/m/$prev2/build.done" ]; do sleep 30; done
  ROOT=$root/m/$m MON=$m PAR=16 bash "$here/tk411-gate-dl.sh" "$list"
  echo "gate-dl-$m rc=$? $(date +%T)" >> "$root/log/run.log"
  prev2=$prev1; prev1=$m
done
