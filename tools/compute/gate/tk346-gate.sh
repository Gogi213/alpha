#!/usr/bin/env bash
# TK-346: гейт «байт в байт» счётчика причин отказа касаний (TouchFilter::reject) + прогон MON 2026-02-10 с ALPHA_REJECT_REASONS.
# Сутки П-24 (8 клеток × 100 символов, p24-day.sh) тремя бинарями: old (0e0dcba8), new (флаг не задан), flag (ALPHA_REJECT_REASONS=list).
# Зелёный = out new == out old и out flag == out old (diff -r). Запуск ТОЛЬКО через alsched:
#   python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk346-gate --max-runtime 1h --cores 1 --mem 24 -- bash /data/tk0346/tk346-gate.sh
# Снимки голов: gate/push-head.sh /data/tk0346/new <дерево new>; … /data/tk0346/old <дерево old>. Итог: /data/tk0346/summary.txt.
set -uo pipefail
W=/data/tk0346; D=2026-02-10; B=$W/calc-build.sh; P=/data/tk0344/p24
S=$W/summary.txt; : > "$S"
# сборки готовы с первого захода (alsched 1022054417821 упал по OOM на трёх параллельных прогонах) — не пересобирать
for v in new old; do [ -x $W/$v/alpha-new ] || { JOBS=4 bash "$B" $W/$v > $W/build-$v.log 2>&1 || { echo "build $v rc≠0" | tee -a "$S"; exit 70; }; }; done
md5sum $W/new/alpha-new $W/old/alpha-new >> "$S"
run() {  # $1 метка $2 бинарь $3.. env
  local tag=$1 bin=$2; shift 2
  rm -rf "$W/run-$tag"; env "$@" BIN="$bin" O="$W/run-$tag" GT=1 CELLS=$P/cells.txt bash $P/p24-day.sh $D; echo "run $tag rc=$?" >> "$S"
}
run old $W/old/alpha-new X=1
run new $W/new/alpha-new X=1
run flag $W/new/alpha-new ALPHA_REJECT_REASONS=list
for t in new flag; do
  if diff -rq "$W/run-old/$D/out" "$W/run-$t/$D/out" > "$W/diff-$t.txt" 2>&1; then echo "diff old/$t: 0 (файлов $(find "$W/run-$t/$D/out" -type f | wc -l))" >> "$S"; else echo "diff old/$t: РАСХОЖДЕНИЕ ($(wc -l < "$W/diff-$t.txt") строк)" >> "$S"; fi
done
grep -E 'reject-(reasons|touch) (MONUSDT|STABLEUSDT) ' "$W/run-flag/$D/grid.log" > "$W/reject-mon-stable.txt"
echo "reject-строк MON/STABLE: $(wc -l < "$W/reject-mon-stable.txt")" >> "$S"
touch "$W/gate.done"
