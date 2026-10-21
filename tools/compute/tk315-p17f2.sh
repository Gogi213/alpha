#!/usr/bin/env bash
# TK-315 П-17 F2: счёт 5 гипотез одним проходом (Г-81/101/135/136/137, 383 клетки: L.cells 10 + L-<мес>.cells 373) по 251 суткам янв–сен, боевой PGO (В-21).
#   План /data/tk0315/p17plan-f2 (tools/registry/p17_f2.py --plan-dir), журнал подходов — готовый /data/tk0341/jrn-b-deleted (251 done), выход /data/tk0315/p17f2/out.
#   Сутки: tk279-p17b-day.sh (журнал jrn-b уже с done — подходы не пересчитываются) → day-cells.sh. env NX (=ядра, умолч. 15), GT=1.
[ -f /data/tk0341/keep/pgo.ok ] || { echo "PGO не готов (нет /data/tk0341/keep/pgo.ok) - пропуск, подать заново"; exit 75; }
export REASON="TK-315 П-17 F2 счёт Г-81/101/135/136/137 (383 хеша), pgo" BIN=/opt/alpha-compute/bin/alpha-tk279-pgo SCRIPTS=/data/tk0231 NX=${NX:-15} GT=${GT:-1}
export NEWJ=/data/tk0341/jrn-b-deleted JRN=/data/tk0341/jrn-b-deleted ALT_JRN= PLAN=L TP=1
R=/data/tk0315/p17f2; mkdir -p "$R/out"; rm -rf "$R/plan"; cp -r /data/tk0315/p17plan-f2 "$R/plan"; cp /data/tk0341/keep/days9.txt "$R/days.txt"
export P=$R/plan O=$R/out
# дельта: сутки без done убиты (OOM) — claim остался бы и day-cells.sh молча пропустил (exit 0); готовые сутки не трогаем
for d in "$R"/out/run-*; do [ -d "$d" ] && [ ! -e "$d/done" ] && rm -rf "$d"; done
xargs -P "$NX" -L1 bash -c 'bash /data/tk0341/keep/p17b-day.sh "run-$1-$2" "$1" "$2"' _ < "$R/days.txt"
rc=$?
n=$(cat "$R"/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
