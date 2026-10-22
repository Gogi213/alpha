#!/usr/bin/env bash
# TK-353: проба C3 «10 монет × неделя» на 15 ядрах (механика и доля сопоставленных, не вердикт). Фев-2026, e-feb tk046, сутки 04..10.
# Шаги: (1) lob touches --r1-cols --wall-log по монета-суткам → кэш $W/cache/<сутки>/{touches,approaches,walls}-SYM.csv;
# (2) bounce-grid --emit-events по суткам (набор B1 дословно = tk332-s0-plan.sh; --touches-from $W/cache) → events-SYM.csv;
# (3) c3-controls по монетам (--events = --wall-events = A_B). Назначаемое — G013-scope/Судья: edges -44.55, max-wait 3600, stride 60,
# caliper 0.1, k 1, seed 20261022, H 1800, окно часа 3600. Тик > 1 бп — монета «не определена» (перечисляется в c3-summary).
#   BIN=/data/tk0353/head/alpha-new bash tk353-probe10.sh      env: W=/data/tk0353/probe10 NX=15   Итог: $W/summary.txt, $W/c3out/<SYM>/
set -uo pipefail
BIN=${BIN:?BIN}; W=${W:-/data/tk0353/probe10}; NX=${NX:-15}; HERE=$(cd "$(dirname "$0")" && pwd)
E=/data/tk046/feb/home/alpha/epochs/e-feb; V=${V:-/data/tk044/final3/verdict.csv}
SYMS="XRPUSDT SOLUSDT DOGEUSDT ADAUSDT LINKUSDT LTCUSDT AVAXUSDT SUIUSDT NEARUSDT BNBUSDT"
DAYS="2026-02-04 2026-02-05 2026-02-06 2026-02-07 2026-02-08 2026-02-09 2026-02-10"
S=$W/summary.txt; mkdir -p "$W/plan" "$W/log" "$W/cache" "$W/grid" "$W/ev" "$W/c3out"; : > "$S"
echo "бинарь: $("$BIN" --version 2>&1 | head -1)  NX=$NX  $(date +%FT%T)" >> "$S"
# (1) журнал стен и подходов
for d in $DAYS; do mkdir -p "$W/cache/$d"; done
for d in $DAYS; do for s in $SYMS; do echo "$d $s"; done; done | xargs -P "$NX" -L1 bash -c '
  d=$0; s=$1; [ -s "'"$W"'/cache/$d/walls-$s.csv" ] && [ -s "'"$W"'/cache/$d/approaches-$s.csv" ] && exit 0
  nice -n 5 "'"$BIN"'" lob touches --root "'"$E"'/study/root-$d" --symbol "$s" --h3-mode notional --h3-usd 10000 --approach-bps 20 \
    --r1-cols --wall-log --out "'"$W"'/cache/$d/touches-$s.csv" > "'"$W"'/log/touches-$d-$s.log" 2>&1 || echo "$d $s TOUCHES_FAIL" >> "'"$W"'/fail.txt"'
echo "(1) журнал: walls=$(ls "$W"/cache/*/walls-*.csv 2>/dev/null | wc -l) approaches=$(ls "$W"/cache/*/approaches-*.csv 2>/dev/null | wc -l) из $(( $(echo $DAYS | wc -w) * $(echo $SYMS | wc -w) )); fail=$(wc -l < "$W/fail.txt" 2>/dev/null || echo 0)" >> "$S"
# (2) события A_B: набор B1 дословно
bash "$HERE/tk332-s0-plan.sh" "$W/plan" > /dev/null
SYMRE=$(echo $SYMS | tr ' ' '|')
grid() {
  d=$1; v="$W/grid/$d/verdict.csv"; mkdir -p "$W/grid/$d"
  awk -F, -v d="$d" -v re="^($SYMRE)\$" 'FNR==1||($2==d&&($1 ~ re))' "$V" > "$v"
  args=(); while read -r f x; do [ -n "$f" ] && args+=("$f" "$x"); done < "$W/plan/s0.args"
  (cd "$E" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$v" --root "study/root-$d" --touches-from "$W/cache" \
    --signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 \
    --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from study/regime --order-usd 500 --carry-root root \
    --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads 2 --hold-step skip --exit-group on --events wide \
    --sigma-from study/sigma240 --r1-cols --exit-form none --early-exit-secs off "${args[@]}" --emit-events \
    --cells "$W/plan/s0.cells" --out-dir "$W/grid/$d/out") > "$W/log/grid-$d.log" 2>&1 || echo "$d GRID_FAIL" >> "$W/fail.txt"
}
export -f grid; export BIN W E V SYMRE
echo $DAYS | tr ' ' '\n' | xargs -P 7 -I{} bash -c 'grid {}'
for s in $SYMS; do
  f=$W/ev/events-$s.csv; echo "arm_ms,side,price_tick" > "$f"
  for d in $DAYS; do for e in $(find "$W/grid/$d/out" -name "events-$s.csv" 2>/dev/null); do tail -n +2 "$e" >> "$f"; done; done
done
echo "(2) события A_B: $(for s in $SYMS; do echo -n "$s=$(( $(wc -l < "$W/ev/events-$s.csv") - 1 )) "; done)" >> "$S"
# (3) c3-controls по монетам
c3() {
  s=$1; a=(); for d in $DAYS; do a+=(--day "$d=$E/study/root-$d/$s-$d.binlog"); done; mkdir -p "$W/c3out/$s"
  nice -n 5 "$BIN" lob c3-controls --symbol "$s" "${a[@]}" --cache "$W/cache" --regime-from "$E/study/regime" --regime-edges-bps=-44.55 \
    --out "$W/c3out/$s" --horizon-s 1800 --tod-window-s 3600 --caliper-sd 0.1 --k 1 --seed 20261022 --max-wait-s 3600 --stride-s 60 \
    --events "$W/ev/events-$s.csv" > "$W/log/c3-$s.log" 2>&1; echo "$s rc=$?" >> "$W/c3.rc"
}
export -f c3; export DAYS; : > "$W/c3.rc"
echo $SYMS | tr ' ' '\n' | xargs -P "$NX" -I{} bash -c 'c3 {}'
echo "(3) c3-controls: $(tr '\n' ' ' < "$W/c3.rc")" >> "$S"
python3 - "$W/c3out" >> "$S" <<'PY'
import json,sys,glob
for f in sorted(glob.glob(sys.argv[1]+"/*/c3-summary*.json")):
    j=json.load(open(f)); print(f.split("/")[-2], {k:j.get(k) for k in ("walls","walls_matched","unmatched_share","controls","candidates","undefined")})
PY
touch "$W/probe10.done"
