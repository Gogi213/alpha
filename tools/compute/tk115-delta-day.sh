#!/usr/bin/env bash
# TK-115 дельта-проход на сутки: ТОЛЬКО символы с сигналами B1 (b1-symdays.csv): touches --r1-cols --wall-log по ним
# (подходы с r1-cols нужны сетке, готовые D20 — 11 колонок), сетка и e106 берут эти подходы (wt/sub/D20). Выход /data/tk0115/delta/<метка>/{walls,signals,e106}/<сутки>/.
#   tk115-delta-day.sh <метка> <мес> <сутки>   env: BIN TP=4 GT=2 FULLREF=1 (полный touches --wall-log сутки как эталон гейта б)
set -uo pipefail
lab=$1; MON=$2; d=$3
BIN=${BIN:-/opt/alpha-compute/bin/alpha-b26tk115r2}; TP=${TP:-4}; GT=${GT:-2}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=/data/tk0115/delta/$lab; V=/data/tk044/final3/verdict.csv
# заявка суток (два задания делят хвост волны без повторного счёта): готовые, идущие и занятые другим заданием — пропуск
mkdir -p "$O"; if [ -e "$O/done" ] || [ -d "$O/w-$d" ] || ! mkdir "$O/claim" 2>/dev/null; then exit 0; fi
mkdir -p "$O/log" "$O/signals/$d" "$O/e106/$d" "$O/walls/$d" "$O/wallsref/$d"; : > "$O/fail.txt"
export HOME=$H BIN O TP
syms=$(awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' /data/tk0115/delta/b1-symdays.csv | sort -u)
allsyms=$(awk -F, -v d="$d" 'NR>1 && $2==d && $17=="пускаем"{print $1}' "$V" | sort -u)
W=$O/w-$d; rm -rf "$W"; mkdir -p "$W/study/approaches" "$W/b5" "$W/bin" "$W/wt"
ln -s "$BIN" "$W/bin/alpha-tk044k1-new"
for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
ln -s "$(readlink -f "$E/study/root-$d")" "$W/study/root-$d"
# верстка: вердикт только по символам B1 этих суток (шапка + строки)
awk -F, -v d="$d" 'NR==FNR{s[$1]=1;next} FNR==1||($2==d&&($1 in s))' <(echo "$syms") "$V" > "$O/verdict-b1.csv"
walls() {  # $1 метка каталога, $2.. символы
  local dir=$1; shift
  for s in "$@"; do echo "$s"; done | xargs -P "$TP" -I{} bash -c 'mkdir -p '"$W"'/wt/'"$dir"'/D20/'"$d"'; nice -n 5 "$BIN" lob touches --root '"$W"'/study/root-'"$d"' --symbol {} --h3-mode notional --h3-usd 10000 --approach-bps 20 --r1-cols --wall-log --out '"$W"'/wt/'"$dir"'/D20/'"$d"'/touches-{}.csv > '"$O"'/log/'"$d"'-{}.'"$dir"'.log 2>&1 || echo "$d {} TOUCHES_FAIL" >> '"$O"'/fail.txt'
}
t0=$(date +%s)
walls sub $syms; t1=$(date +%s)
[ "${FULLREF:-0}" = 1 ] && { walls full $allsyms; }
t2=$(date +%s)
common=(--signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --entry-ttl-secs 1800 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide --entry-form ladder3x0..0.0409sw2 --sigma-from study/sigma240)
(cd "$W" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$O/verdict-b1.csv" --root "study/root-$d" --touches-from wt/sub/D20 "${common[@]}" \
  --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --r1-cols --tape-log 30 --out-dir "b5/out-$d" > "$O/log/$d.grid.log" 2>&1) || echo "$d GRID_FAIL" >> "$O/fail.txt"
t3=$(date +%s)
cf=$O/e106-$d.cells
printf '%s\n' ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800{,-nostop1,-nostop2,-nostop4} | sed 's/$/ e106-pop/' > "$cf"
(cd "$W" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$O/verdict-b1.csv" --root "study/root-$d" --touches-from wt/sub/D20 "${common[@]}" --r1-cols \
  --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 --exit-form none --exit-form nostop1 --exit-form nostop2 --exit-form nostop4 \
  --set e106-pop:age=2700,side=bid,btc4h_max=-44.55,r1_born_shift_cbps_min=-9000000000000000000 --cells "$cf" --out-dir "b5/e106-$d" > "$O/log/$d.e106.log" 2>&1) || echo "$d E106_FAIL" >> "$O/fail.txt"
t4=$(date +%s)
(cd "$W/b5/out-$d" && find . -type f \( -name 'signals*.csv' -o -name 'rounds*.csv' \) -exec cp --parents -t "$O/signals/$d" {} +)
cp -r "$W/b5/e106-$d"/. "$O/e106/$d"/
cp "$W"/wt/sub/D20/$d/walls-*.csv "$O/walls/$d"/ 2>/dev/null || echo "$d WALLS_SUB_NOOUT" >> "$O/fail.txt"
[ "${FULLREF:-0}" = 1 ] && cp "$W"/wt/full/D20/$d/walls-*.csv "$O/wallsref/$d"/ 2>/dev/null
echo "$d syms_b1=$(echo "$syms"|grep -c .) syms_all=$(echo "$allsyms"|grep -c .) walls_sub_s=$((t1-t0)) walls_full_s=$((t2-t1)) b1grid_s=$((t3-t2)) e106_s=$((t4-t3))" >> "$O/units.txt"
rm -rf "$W"
touch "$O/done"
