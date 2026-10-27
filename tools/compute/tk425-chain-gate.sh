#!/bin/bash
# TK-425 (G-017, п.5): Gate июль — архив сделок 202607 после закачки августа TK-423 (потоки ≤16 суммарно, бан 23.10 при 32).
# Использование: tk425-chain-gate.sh <rc-файл TK-423 gate-dl>   (ROOT=/srv/alpha-sda/tk425/jul-gate)
set -u
rc=$1; root=${ROOT:-/srv/alpha-sda/tk425/jul-gate}; here=$(dirname "$(readlink -f "$0")"); mkdir -p "$root"
until [ -s "$rc" ]; do sleep 30; done
ROOT=$root MON=202607 PAR=16 bash "$here/tk411-gate-dl.sh" /data/tk0393/tk423/gate-list.tsv
