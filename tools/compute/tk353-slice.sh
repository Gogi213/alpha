#!/usr/bin/env bash
# TK-353: техническая проба lob c3-controls (механика, не вердикт): 1 монета × 5 суток фев-2026 (e-feb tk046).
# Шаг 1 — журнал: `lob touches --r1-cols --wall-log` по суткам (даёт approaches-/walls-SYM.csv в кэше), шаг 2 — c3-controls.
# Назначаемые из G013-scope.json: caliper 0.1, k 1, seed 20261022, окно часа 1 ч. НЕ назначены в scope (плейсхолдеры пробы,
# значения решает Судья): --regime-edges-bps (один класс), --max-wait-s, --stride-s. H=1800 (barriers_primary.H_s).
#   tk353-slice.sh   env: BIN SYM=SOLUSDT W=/data/tk0353/slice   Итог: $W/summary.txt, $W/c3out/
set -uo pipefail
BIN=${BIN:-/data/tk0353/new/alpha-new}; SYM=${SYM:-SOLUSDT}; W=${W:-/data/tk0353/slice}
E=/data/tk046/feb/home/alpha/epochs/e-feb; DAYS="2026-02-06 2026-02-07 2026-02-08 2026-02-09 2026-02-10"
S=$W/summary.txt; mkdir -p "$W/cache" "$W/c3out"; : > "$S"
args=()
for d in $DAYS; do
  f=$E/study/root-$d/$SYM-$d.binlog
  [ -e "$f" ] || { echo "нет $f" | tee -a "$S"; exit 70; }
  mkdir -p "$W/cache/$d"
  nice -n 5 "$BIN" lob touches --root "$E/study/root-$d" --symbol "$SYM" --h3-mode notional --h3-usd 10000 --approach-bps 20 \
    --r1-cols --wall-log --out "$W/cache/$d/touches-$SYM.csv" > "$W/touches-$d.log" 2>&1 || { echo "touches $d rc≠0" | tee -a "$S"; exit 71; }
  args+=(--day "$d=$f")
done
echo "журнал: $(ls "$W"/cache/*/walls-$SYM.csv | wc -l) walls, $(ls "$W"/cache/*/approaches-$SYM.csv | wc -l) approaches" >> "$S"
nice -n 5 "$BIN" lob c3-controls --symbol "$SYM" "${args[@]}" --cache "$W/cache" --regime-from "$E/study/regime" \
  --regime-edges-bps=-1000000 --out "$W/c3out" --horizon-s 1800 --tod-window-s 3600 --caliper-sd 0.1 --k 1 --seed 20261022 \
  --max-wait-s 1800 --stride-s 60 > "$W/c3.log" 2>&1
echo "c3-controls rc=$?" >> "$S"
tail -5 "$W/c3.log" >> "$S"; ls "$W/c3out" | head -20 >> "$S"
touch "$W/slice.done"
