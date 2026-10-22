#!/usr/bin/env bash
# TK-279 П-17: Г-98 (9 клеток size_risk=R) на НОВЫХ подходах jrn-b (stack_span_bps_at_arm есть; старый кэш /data/p12r3a/jrn без колонки — Г-98 вырожден, 15:10 17.10).
#   Подходы jrn-b готовы на все 251 сутки (done) -> p17b-day.sh сразу идёт в day-cells.sh; затем psim cap 0/3 и сводка /data/tk0279/p17g98-sum.csv. env NX (=ядра, умолч. 3).
[ -f /data/tk0279/pgo.ok ] || { echo "PGO не готов (нет /data/tk0279/pgo.ok)"; exit 75; }
export REASON="TK-279 П-17 Г-98 на jrn-b, pgo" BIN=/opt/alpha-compute/bin/alpha-tk279-pgo SCRIPTS=/data/tk0231 NX=${NX:-3} GT=${GT:-1}
export NEWJ=/data/tk0279/jrn-b JRN=/data/tk0279/jrn-b ALT_JRN= PLAN=L TP=1
A=/data/tk0279/p17plan-a2; PS=/data/tk0279/p17plan-g98; R=/data/tk0279/p17g98
rm -rf "$PS"; mkdir -p "$PS" "$R/out"; cp "$A/L.args" "$A/L.cells" "$PS/"   # L.cells = 9 клеток Г-98; месячные файлы пустые (day-cells.sh их cat-ает)
for m in 1 2 3 4 5 6 7 8 9; do : > "$PS/L-2026-0$m.cells"; : > "$PS/L-2026-0$m.args"; done
rm -rf "$R/plan"; cp -r "$PS" "$R/plan"; cp /data/tk0279/days9.txt "$R/days.txt"
export P=$R/plan O=$R/out
for d in "$R"/out/run-*; do [ -d "$d" ] && [ ! -e "$d/done" ] && rm -rf "$d"; done
xargs -P "$NX" -L1 bash -c 'bash /data/tk0279/p17b-day.sh "run-$1-$2" "$1" "$2"' _ < "$R/days.txt"
rc=$?
n=$(cat "$R"/out/*/fail.txt 2>/dev/null | wc -l)
[ "$n" = 0 ] || { echo "fail.txt: $n строк"; rc=3; }
[ "$rc" = 0 ] || exit $rc
export PM=${PM:-$NX}
bash /opt/alpha-launch/tools/launch/psim-launch.sh "$R" || exit $?
python3 /data/tk0231/sum_closes_f1.py "$R/psim" /data/tk0279/p17g98-sum.csv closes-p14f1
