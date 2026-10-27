#!/bin/bash
# TK-428 п.3 (G-017): сборка свечей 1 м Gate по месяцам 202601–202606 из сырья tk428-chain-gate-dl.sh (сырьё после сборки удаляется).
# Использование: tk428-chain-gate-build.sh [N потоков=4]   (ROOT=/srv/alpha-sda/tk428)
set -u
root=${ROOT:-/srv/alpha-sda/tk428}; list=${LIST:-/data/tk0393/tk423/gate-list.tsv}; here=$(dirname "$(readlink -f "$0")"); n=${1:-4}
for m in 202601 202602 202603 202604 202605 202606; do
  ROOT=$root/m/$m bash "$here/tk411-gate-build.sh" "$list" "$n"
  echo "gate-build-$m rc=$? $(date +%T)" >> "$root/log/run.log"
done
