#!/usr/bin/env bash
# TK-279 П-17: счёт Г-135/136/137 (103 клетки на сутки: L.cells 58 + L-<мес>.cells 45) с пересчётом подходов (ABN3) по каждым суткам, боевой PGO (В-21).
#   План /data/tk0279/p17plan-b, новый журнал /data/tk0279/jrn-b, выход /data/tk0279/p17b/out; env NX (=ядра, умолч. 12), GT=1.
[ -f /data/tk0279/pgo.ok ] || { echo "PGO не готов (нет /data/tk0279/pgo.ok) - пропуск, подать заново"; exit 75; }
export REASON="TK-279 П-17 счёт Г-135/136/137, новые подходы, pgo" BIN=/opt/alpha-compute/bin/alpha-tk279-pgo SCRIPTS=/data/tk0231 NX=${NX:-12} GT=${GT:-1}
export NEWJ=/data/tk0279/jrn-b JRN=/data/tk0279/jrn-b ALT_JRN= PLAN=L TP=1
R=/data/tk0279/p17b; mkdir -p "$R/out"; rm -rf "$R/plan"; cp -r /data/tk0279/p17plan-b "$R/plan"; cp /data/tk0279/days9.txt "$R/days.txt"
export P=$R/plan O=$R/out
# дельта (rc143 OOM 17.10): сутки без done убиты — claim остался бы и day-cells.sh молча пропустил (exit 0); готовые сутки не трогаем
for d in "$R"/out/run-*; do [ -d "$d" ] && [ ! -e "$d/done" ] && rm -rf "$d"; done
xargs -P "$NX" -L1 bash -c 'bash /data/tk0279/p17b-day.sh "run-$1-$2" "$1" "$2"' _ < "$R/days.txt"
rc=$?
n=$(cat "$R"/out/*/fail.txt "$NEWJ"/*/new/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
exit $rc
