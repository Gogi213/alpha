#!/bin/bash
# TK-428 (возврат Судьи 3, п.1): Gate фандинг-архив funding_{updates,applies} 202601–202607 по 140 делистнутым фьючерсам вне gate-list
# (список = tk428z/gate-oi/list.csv, колонка 3). Тот же загрузчик, что п.2 (tk428-gate-funding.sh); не-200 -> $ROOT/log/miss.tsv (вид, месяц, контракт, код).
# Использование: alsched submit --kind net --name tk428-fund-extra -- bash /data/tk0428/src/tk428-gate-funding-extra.sh   (ROOT=/srv/alpha-sda/tk428z/gate-funding, PAR=8)
set -u
root=${ROOT:-/srv/alpha-sda/tk428z/gate-funding}; src=$(dirname "$(readlink -f "$0")")
mkdir -p "$root/log"
awk -F, 'NR>1 && $3!="" {print "fut\t"$3"\t1"}' /srv/alpha-sda/tk428z/gate-oi/list.csv > "$root/list.tsv"
ROOT="$root" LIST="$root/list.tsv" PAR="${PAR:-8}" bash "$src/tk428-gate-funding.sh"
