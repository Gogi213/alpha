#!/usr/bin/env bash
# TK-366 (G-016): весь проход — K шардов tk366-pass.sh параллельно под одной заявкой alsched. tk366-all.sh [K=15]; env JOBS (умолч. 2 на шард)
K=${1:-15}; export JOBS=${JOBS:-2}
for i in $(seq 0 $((K-1))); do bash /srv/alpha-sda/tk366/tk366-pass.sh "$i" "$K" & done
wait
