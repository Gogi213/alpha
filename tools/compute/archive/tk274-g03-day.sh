#!/usr/bin/env bash
# TK-274 Г-03 (П-15 §5–6): сутки B1 — реплей touches с двойниками (--twin-seeds 1..5) и B1 на стенах/двойниках.
#   tk274-g03-day.sh <метка> <мес> <сутки>   env: BIN (pgo) TP=4 GT=2 P (план: g03.cells + g03.args) O (выход) JRN (кэш подходов для сверки)
# Реплей — те же флаги, что у кэша подходов TK-115 (tk115-jrn-day.sh) + --twin-seeds: approaches-*.csv сверяются побайтно с кэшем (cmp.txt).
# Затем B1 (одна клетка) × 5 seed × {random, paired}: общий каталог выхода $O/out/<сутки>/<режим><seed>/ (forms.csv, rounds.csv, level-control-*.csv).
#   MODES="random paired" (умолч.); режим nowall = `--level-control random:<seed>:nowall` (двойник без отмены по гибели стены, Судья 17.10).
#   FROM=<каталог выхода прежнего прохода>: реплея нет — approaches берутся из кэша TK-115 ($JR; читатель колонку stack_span не требует),
#   двойники — из $FROM/<метка>/twins/<сутки>.tgz (те же файлы, что писал реплей); сверка с кэшем не нужна (она прошла в прежнем проходе).
set -uo pipefail
lab=$1; MON=$2; d=$3
BIN=${BIN:?BIN обязателен}; TP=${TP:-4}; GT=${GT:-2}; P=${P:?P обязателен}; OUT=${O:?O обязателен}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=$OUT/$lab; V=/data/tk044/final3/verdict.csv; B1=${B1:-/data/tk0115/delta/b1-symdays.csv}; RG=${RG:-/data/tk0196/regime}
python3 /data/sched/manifest.py fastbin "$BIN" >&2 || exit 3
mkdir -p "$O"; if [ -e "$O/done" ] || ! mkdir "$O/claim" 2>/dev/null; then exit 0; fi
mkdir -p "$O/log" "$O/out"; : > "$O/fail.txt"; : > "$O/cmp.txt"
export HOME=$H BIN O TP
syms=$(awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' "$B1" | sort -u)
JR=${JRN:-/data/p12r3a/jrn}; [ -e "$JR/$d/done" ] || JR=/srv/alpha-sda/jrn
W=$O/w-$d; rm -rf "$W"; mkdir -p "$W/study" "$W/b5" "$W/bin" "$W/wt/sub/D20/$d"
ln -s "$BIN" "$W/bin/alpha-tk044k1-new"
for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches|regime) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
RS=$(readlink -f "$E/study/root-$d"); AR=${ALT_ROOT-/srv/alpha-sda/root}/root-$d
sig() { (cd "$1" && stat -L -c '%n %s %Y' * .[!.]* 2>/dev/null | LC_ALL=C sort); }
if [ -d "$AR" ] && [ "$(sig "$RS")" = "$(sig "$AR")" ]; then RS=$AR; fi
ln -s "$RS" "$W/study/root-$d"
[ -e "$RG/$MON/$d.csv" ] || { echo "$d НЕТ РЕЖИМА $RG/$MON" >> "$O/fail.txt"; exit 4; }; ln -s "$RG/$MON" "$W/study/regime"
awk -F, -v d="$d" 'NR==FNR{s[$1]=1;next} FNR==1||($2==d&&($1 in s))' <(echo "$syms") "$V" > "$O/verdict-b1.csv"
t0=$(date +%s)
D20=$W/wt/sub/D20/$d
if [ -n "${FROM:-}" ]; then   # без реплея: кэш подходов TK-115 + двойники прежнего прохода
  [ -f "$FROM/$lab/twins/$d.tgz" ] || { echo "$d нет $FROM/$lab/twins/$d.tgz" >> "$O/fail.txt"; exit 5; }
  tar xzf "$FROM/$lab/twins/$d.tgz" -C "$D20"
  for s in $syms; do ln -s "$JR/$d/wt/sub/D20/$d/approaches-$s.csv" "$D20/approaches-$s.csv" || echo "$d $s НЕТ КЭША" >> "$O/fail.txt"; done
  t1=$(date +%s)
else
for s in $syms; do echo "$s"; done | xargs -P "$TP" -I{} bash -c 'nice -n 5 "$BIN" lob touches --root '"$W"'/study/root-'"$d"' --symbol {} --h3-mode notional --h3-usd 10000 --approach-bps 20 --r1-cols --wall-log --twin-seeds 1,2,3,4,5 --out '"$W"'/wt/sub/D20/'"$d"'/touches-{}.csv > '"$O"'/log/'"$d"'-{}.touches.log 2>&1 || echo "'"$d"' {} TOUCHES_FAIL" >> '"$O"'/fail.txt'
t1=$(date +%s)
for s in $syms; do
  a=$W/wt/sub/D20/$d/approaches-$s.csv; b=$JR/$d/wt/sub/D20/$d/approaches-$s.csv
  if [ -f "$b" ]; then
    # кэш TK-115 старше колонки stack_span_bps_at_arm (TK-279): новый файл сверяем без неё, остальное — побайтно
    ci=$(head -n1 "$a" | tr ',' '
' | grep -nx stack_span_bps_at_arm | cut -d: -f1)
    if [ -n "$ci" ]; then cut -d, --complement -f"$ci" "$a" | cmp -s - "$b"; else cmp -s "$a" "$b"; fi       && echo "$s same" >> "$O/cmp.txt" || echo "$s DIFF" >> "$O/cmp.txt"
  else echo "$s nocache" >> "$O/cmp.txt"; fi
done
fi
common=(--signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide --sigma-from study/sigma240)
args=(); while read -r f v; do [ -n "$f" ] && args+=("$f" "$v"); done < <(sort -u "$P/g03.args")
for seed in 1 2 3 4 5; do for mode in ${MODES:-random paired}; do
  lc=$mode:$seed; [ "$mode" = nowall ] && lc=random:$seed:nowall
  (cd "$W" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$O/verdict-b1.csv" --root "study/root-$d" --touches-from wt/sub/D20 "${common[@]}" --r1-cols --exit-form none --early-exit-secs off "${args[@]}" --level-control "$lc" --cells "$P/g03.cells" --out-dir "b5/out-$mode$seed" > "$O/log/$d.grid-$mode$seed.log" 2>&1) || echo "$d GRID_FAIL $mode$seed" >> "$O/fail.txt"
  mkdir -p "$O/out/$d/$mode$seed"; cp -r "$W/b5/out-$mode$seed"/. "$O/out/$d/$mode$seed"/ 2>/dev/null
done; done
t2=$(date +%s)
[ -n "${FROM:-}" ] || { mkdir -p "$O/twins"; (cd "$W/wt/sub/D20/$d" && tar czf "$O/twins/$d.tgz" twins-*.csv) 2>/dev/null; }
echo "$d syms_b1=$(echo "$syms"|grep -c .) touches_s=$((t1-t0)) grid_s=$((t2-t1)) diff=$(grep -c DIFF "$O/cmp.txt")" >> "$OUT/units.txt"
rm -rf "$W"; touch "$O/done"
