#!/usr/bin/env bash
# TK-289 П-18 шаг 4: стопка (2 кл.) + F3 осей 6а (8) и 6в (72) = 82 клетки на сутки (738 хеш-мес.); сетка docs/findings/tk289-f3-grid-2026-10-21.csv
#   (tk289-stack-f3.py), план docs/findings/tk289-f3-plan-2026-10-21 (метка P-18-F2, p18_plan.py --axes 6a,6v,stack --f2). F3 оси 5 (ttl, 8 точек) здесь НЕТ:
#   значения вне ENTRY_TTL_SECS бинарника — отдельным шагом после расширения сетки (как F2 оси 4/5).
#   Выход $R/out = /data/tk0289/f3/out/run-<мес>-<сутки>/ ; env NX (=ядра, умолч. 15), GT=1. Повторный запуск — дельта: сутки без done убираются.
set -uo pipefail
export REASON="TK-289 П-18 стопка + F3 осей 6а,6в, 82 клетки, pgo" BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk0289b-pgo} SCRIPTS=/data/tk0231 NX=${NX:-15} GT=${GT:-1} PLAN=P-18-F2 TP=1
R=${R:-/data/tk0289/f3}; PLANSRC=${PLANSRC:-/data/tk0289/f3plan}; mkdir -p "$R/out"; rm -rf "$R/plan"; cp -r "$PLANSRC" "$R/plan"; : > "$R/plan/$PLAN.cells"; : > "$R/plan/$PLAN.args"; echo 2026-01 > "$R/plan/months.txt"
cp /data/tk0341/keep/days9.txt "$R/days.txt"
export P=$R/plan O=$R/out
for d in "$R"/out/run-*; do [ -d "$d" ] && [ ! -e "$d/done" ] && rm -rf "$d"; done
xargs -P "$NX" -L1 bash -c 'bash "$0/day-cells.sh" "run-$1-$2" "$1" "$2"' "$SCRIPTS" < "$R/days.txt"
rc=$?
n=$(cat "$R"/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
