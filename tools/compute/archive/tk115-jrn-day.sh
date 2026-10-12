#!/usr/bin/env bash
# TK-115 журнал R2-B на исполнении (решение Судьи 16:45, вариант б — без кода): B1-символо-сутки, touches --r1-cols --wall-log
# КЛАДЁТСЯ насовсем ($O/touches/<сутки>, R2-B берёт их --touches-from без пересчёта), затем база B1 (1 клетка) дважды:
# --tape-log 60 → w60/ и --tape-log 15 → w15/ (rounds.csv: tape_press_lots, cxl_press_lots, chase_wait_ms на исполнении, окно = размер кольца).
# Окно 30 — готовый источник v5 (/data/tk0115/delta/w). Код не менялся: сборка alpha-tk115f.
#   tk115-jrn-day.sh <метка> <мес> <сутки>   env: BIN TP=4 GT=2 O=/data/p12r3a/jrn
set -uo pipefail
lab=$1; MON=$2; d=$3
BIN=${BIN:-/data/tk0115/bin/alpha-tk115f}; TP=${TP:-4}; GT=${GT:-2}; OUT=${O:-/data/p12r3a/jrn}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=$OUT/$lab; V=/data/tk044/final3/verdict.csv; B1=${B1:-/data/tk0115/delta/b1-symdays.csv}
mkdir -p "$O"; if [ -e "$O/done" ] || ! mkdir "$O/claim" 2>/dev/null; then exit 0; fi
mkdir -p "$O/log"; : > "$O/fail.txt"
export HOME=$H BIN O TP
ym=2026-$(case $MON in jan) echo 01;; feb) echo 02;; mar) echo 03;; apr) echo 04;; may) echo 05;; jun) echo 06;; jul) echo 07;; aug) echo 08;; sep) echo 09;; oct) echo 10;; esac)
syms=$(awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' "$B1" | sort -u)
W=$O/w-$d; rm -rf "$W"; mkdir -p "$W/study/approaches" "$W/b5" "$W/bin" "$O/wt"; ln -s "$O/wt" "$W/wt"
ln -s "$BIN" "$W/bin/alpha-tk044k1-new"
for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
ln -s "$(readlink -f "$E/study/root-$d")" "$W/study/root-$d"
awk -F, -v d="$d" 'NR==FNR{s[$1]=1;next} FNR==1||($2==d&&($1 in s))' <(echo "$syms") "$V" > "$O/verdict-b1.csv"
walls() {
  for s in $syms; do echo "$s"; done | xargs -P "$TP" -I{} bash -c 'mkdir -p '"$W"'/wt/sub/D20/'"$d"'; nice -n 5 "$BIN" lob touches --root '"$W"'/study/root-'"$d"' --symbol {} --h3-mode notional --h3-usd 10000 --approach-bps 20 --r1-cols --wall-log --out '"$W"'/wt/sub/D20/'"$d"'/touches-{}.csv > '"$O"'/log/'"$d"'-{}.sub.log 2>&1 || echo "'"$d"' {} TOUCHES_FAIL" >> '"$O"'/fail.txt'
}
t0=$(date +%s); walls; t1=$(date +%s)
common=(--signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --entry-ttl-secs 1800 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide --sigma-from study/sigma240)
grid() {  # $1 окно журнала, с
  (cd "$W" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$O/verdict-b1.csv" --root "study/root-$d" --touches-from wt/sub/D20 "${common[@]}" --r1-cols     --entry-form ladder3x0..0.0409sw2 --stop-form pct2 --take-form tr1x1 --deadline-secs 14400     --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --tape-log "$1" --out-dir "b5/out$1-$d" > "$O/log/$d.grid$1.log" 2>&1) || echo "$d GRID${1}_FAIL" >> "$O/fail.txt"
  mkdir -p "$O/w$1/$d"; cp -r "$W/b5/out$1-$d"/. "$O/w$1/$d"/
}
grid 60; grid 15
t2=$(date +%s)
echo "$d syms_b1=$(echo "$syms"|grep -c .) touches_s=$((t1-t0)) grid_s=$((t2-t1))" >> "$OUT/units.txt"
rm -rf "$W"; touch "$O/done"
