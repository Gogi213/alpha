#!/bin/bash
# TK-425: после сборки Gate-свечей июля (rc задания) — сводки охвата июля (Bybit 24–31.07, Gate весь июль) и отчёт.
# Использование: tk425-final.sh <rc-файл сборки Gate>   (ROOT=/srv/alpha-sda/tk425)
set -u
rc=$1; root=${ROOT:-/srv/alpha-sda/tk425}; here=$(dirname "$(readlink -f "$0")")
until [ -s "$rc" ]; do sleep 30; done
: > "$root/log/empty-heads.tsv"
K_START=1784851200000 K_END=1785542400000 python3 -I "$here/tk411-finalize.py" "$root/jul" "$root/log/empty-heads.tsv" "$root/jul/coverage.csv" > "$root/log/fin-bybit.txt" 2>&1
K_START=1782864000000 K_END=1785542400000 python3 -I "$here/tk411-finalize.py" "$root/jul-gate" "$root/jul-gate/log/miss.tsv" "$root/jul-gate/coverage.csv" > "$root/log/fin-gate.txt" 2>&1
python3 -I "$here/tk425-report.py" "$root" > "$root/report.md" 2> "$root/log/report.err"
echo "final rc=$? $(date +%T)" >> "$root/log/run.log"
