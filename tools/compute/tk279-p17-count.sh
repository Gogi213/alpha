#!/usr/bin/env bash
# TK-279 П-17: счёт 5 гипотез без Г-81 (Г-98/101/135/136/137), 229 клеток на сутки: L.cells (набор Г-98/136/137) + L-<мес>.cells (Г-101/135 по порогу месяца).
#   tk279-p17-count.sh <каталог прохода> <файл суток "мес ГГГГ-ММ-ДД"> ; env BIN (r2), SCRIPTS (day-cells.sh), PLANSRC (план-каталог), NX, GT
set -uo pipefail
R=$1; DAYS=$2; S=${SCRIPTS:-/data/tk0231}; PS=${PLANSRC:-/data/tk0279/p17plan}; NX=${NX:-12}
mkdir -p "$R/out"; rm -rf "$R/plan"; cp -r "$PS" "$R/plan"; cp "$DAYS" "$R/days.txt"
export PLAN=L P=$R/plan O=$R/out BIN GT=${GT:-1} TP=1
xargs -P "$NX" -L1 bash -c 'bash "$0/day-cells.sh" "run-$1-$2" "$1" "$2"' "$S" < "$R/days.txt"
