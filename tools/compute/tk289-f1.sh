#!/usr/bin/env bash
# TK-289 П-18 F1: титровка базы B1 по осям 1–5, 6а, 7 — 53 клетки на сутки (план /data/tk0289/f1plan: L-<мес>.cells; ttl 300–1200 уже в Летописи), 251 сутки, боевой PGO (В-21).
#   Выход /data/tk0289/f1/out/run-<мес>-<сутки>/ ; env NX (=ядра, умолч. 15), GT=1. Повторный запуск — дельта: сутки без done убираются, готовые не трогаются.
set -uo pipefail
export REASON="TK-289 П-18 F1 оси 1-5,6а,7, 53 клетки, pgo" BIN=/opt/alpha-compute/bin/alpha-tk289-pgo SCRIPTS=/data/tk0231 NX=${NX:-15} GT=${GT:-1} PLAN=L TP=1
R=/data/tk0289/f1; mkdir -p "$R/out"; rm -rf "$R/plan"; cp -r /data/tk0289/f1plan "$R/plan"; : > "$R/plan/L.cells"; : > "$R/plan/L.args"; echo 2026-01 > "$R/plan/months.txt"
cp /data/tk0341/keep/days9.txt "$R/days.txt"
export P=$R/plan O=$R/out
for d in "$R"/out/run-*; do [ -d "$d" ] && [ ! -e "$d/done" ] && rm -rf "$d"; done
xargs -P "$NX" -L1 bash -c 'bash "$0/day-cells.sh" "run-$1-$2" "$1" "$2"' "$SCRIPTS" < "$R/days.txt"
rc=$?
n=$(cat "$R"/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
