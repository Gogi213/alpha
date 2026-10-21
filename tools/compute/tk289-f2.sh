#!/usr/bin/env bash
# TK-289 П-18 F2: сужение по осям 1–5, 6а, 7, 6в — 141 клетка на сутки (план /data/tk0289/f2plan = docs/findings/tk289-f2-plan-2026-10-21, метка P-18-F2; 6б и ось 8 — отдельными заказами), 251 сутки, боевой PGO (В-21).
#   Выход /data/tk0289/f2/out/run-<мес>-<сутки>/ ; env NX (=ядра, умолч. 15), GT=1. Повторный запуск — дельта: сутки без done убираются, готовые не трогаются.
set -uo pipefail
export REASON="TK-289 П-18 F2 оси 1-5,6а,7,6в, 141 клетка, pgo" BIN=/opt/alpha-compute/bin/alpha-tk289-pgo SCRIPTS=/data/tk0231 NX=${NX:-15} GT=${GT:-1} PLAN=P-18-F2 TP=1
R=/data/tk0289/f2; mkdir -p "$R/out"; rm -rf "$R/plan"; cp -r /data/tk0289/f2plan "$R/plan"; : > "$R/plan/$PLAN.cells"; : > "$R/plan/$PLAN.args"; echo 2026-01 > "$R/plan/months.txt"
cp /data/tk0279/days9.txt "$R/days.txt"
export P=$R/plan O=$R/out
for d in "$R"/out/run-*; do [ -d "$d" ] && [ ! -e "$d/done" ] && rm -rf "$d"; done
xargs -P "$NX" -L1 bash -c 'bash "$0/day-cells.sh" "run-$1-$2" "$1" "$2"' "$SCRIPTS" < "$R/days.txt"
rc=$?
n=$(cat "$R"/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
