#!/usr/bin/env bash
# TK-353: гейт «байт в байт» ветки tk353 (c3-controls; bounce_grid.rs — только видимость re-export): old = merge-base 7ff7f8d7,
# new = голова tk353. Сутки П-24 2026-02-10 × 8 клеток × 100 символов (p24-day.sh), прогоны подряд (3 параллельных → OOM).
# Зелёный = diff -r out old/new пуст. Запуск: python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk353-gate --max-runtime 1h --cores 4 --mem 24 -- bash /data/tk0353/tk353-gate.sh
# Снимки голов: gate/push-head.sh /data/tk0353/{new,old} <дерево>. Итог: /data/tk0353/summary.txt.
set -uo pipefail
W=/data/tk0353; D=2026-02-10; B=/data/tk0346/calc-build.sh; P=/data/tk0344/p24
S=$W/summary.txt; : > "$S"
for v in new old; do [ -x $W/$v/alpha-new ] || { JOBS=4 bash "$B" $W/$v > $W/build-$v.log 2>&1 || { echo "build $v rc≠0" | tee -a "$S"; exit 70; }; }; done
md5sum $W/new/alpha-new $W/old/alpha-new >> "$S"
run() { local tag=$1 bin=$2; rm -rf "$W/run-$tag"; BIN="$bin" O="$W/run-$tag" GT=1 CELLS=$P/cells.txt bash $P/p24-day.sh $D; echo "run $tag rc=$?" >> "$S"; }
run old $W/old/alpha-new
run new $W/new/alpha-new
if diff -rq "$W/run-old/$D/out" "$W/run-new/$D/out" > "$W/diff-new.txt" 2>&1; then echo "diff old/new: 0 (файлов $(find "$W/run-new/$D/out" -type f | wc -l))" >> "$S"; else echo "diff old/new: РАСХОЖДЕНИЕ ($(wc -l < "$W/diff-new.txt") строк)" >> "$S"; fi
touch "$W/gate.done"
