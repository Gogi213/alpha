#!/usr/bin/env bash
# TK-348: гейт «байт в байт» слияния master+tk346 (sets.rs сведён) — сутки П-24 (8 клеток × 100 символов, p24-day.sh)
# двумя бинарями: old (голова кода tk346 88b2bb3c, /data/tk0346/new) и new (голова tk348). Зелёный = diff -r out old == out new.
# Запуск ТОЛЬКО через alsched:
#   python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk348-gate --max-runtime 1h --cores 1 --mem 24 -- bash /data/tk0348/tk348-gate.sh
# Снимок головы: gate/push-head.sh /data/tk0348/new <дерево tk348>. Итог: /data/tk0348/summary.txt.
set -uo pipefail
W=/data/tk0348; D=2026-02-10; B=/data/tk0346/calc-build.sh; P=/data/tk0344/p24
S=$W/summary.txt; : > "$S"
mkdir -p $W/old; [ -x $W/old/alpha-new ] || cp /data/tk0346/new/alpha-new $W/old/alpha-new
[ -x $W/new/alpha-new ] || { JOBS=4 bash "$B" $W/new > $W/build-new.log 2>&1 || { echo "build new rc≠0" | tee -a "$S"; exit 70; }; }
md5sum $W/new/alpha-new $W/old/alpha-new >> "$S"; $W/new/alpha-new --version >> "$S" 2>&1
run() {  # $1 метка $2 бинарь
  rm -rf "$W/run-$1"; env BIN="$2" O="$W/run-$1" GT=1 CELLS=$P/cells.txt bash $P/p24-day.sh $D; echo "run $1 rc=$?" >> "$S"
}
run old $W/old/alpha-new
run new $W/new/alpha-new
if diff -rq "$W/run-old/$D/out" "$W/run-new/$D/out" > "$W/diff.txt" 2>&1; then echo "diff old/new: 0 (файлов $(find "$W/run-new/$D/out" -type f | wc -l))" >> "$S"; else echo "diff old/new: РАСХОЖДЕНИЕ ($(wc -l < "$W/diff.txt") строк)" >> "$S"; fi
touch "$W/gate.done"
