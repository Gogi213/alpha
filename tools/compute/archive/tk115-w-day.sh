#!/usr/bin/env bash
# TK-115 замер W (Г-133, r2-spec стр. 215): сетка B1 одной формой выхода chase<W> (окно — до конца суток), только символы B1 суток
# (b1-symdays.csv), готовые подходы D20 прежних проходов (r1-колонки не нужны), --tape-log 30 ради колонки chase_wait_ms.
#   tk115-w-day.sh <мес> <сутки>   env: BIN GT=2 CHASE_MS=86400000   → /data/tk0115/delta/w/<сутки>/{rounds,forms}…, маркер done
set -uo pipefail
MON=$1; d=$2
BIN=${BIN:-/opt/alpha-compute/bin/alpha-b26tk115r2}; GT=${GT:-2}; CHASE_MS=${CHASE_MS:-86400000}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=/data/tk0115/delta/w/$d; V=/data/tk044/final3/verdict.csv
mkdir -p "$O"; if [ -e "$O/done" ] || ! mkdir "$O/claim" 2>/dev/null; then exit 0; fi
export HOME=$H
syms=$(awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' /data/tk0115/delta/b1-symdays.csv | sort -u)
awk -F, -v d="$d" 'NR==FNR{s[$1]=1;next} FNR==1||($2==d&&($1 in s))' <(echo "$syms") "$V" > "$O/verdict-b1.csv"
W=$O/w-$d; rm -rf "$W"; mkdir -p "$W/b5" "$W/bin"
ln -s "$BIN" "$W/bin/alpha-tk044k1-new"
for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
t0=$(date +%s)
(cd "$W" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$O/verdict-b1.csv" --root "study/root-$d" --touches-from "$E/study/approaches/D20" \
  --signal approach --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
  --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --entry-ttl-secs 1800 --band-exit-bps 20 \
  --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide --entry-form ladder3x0..0.0409sw2 --sigma-from study/sigma240 \
  --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 --exit-form "chase$CHASE_MS" \
  --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --tape-log 30 --out-dir "b5/out-$d" > "$O/grid.log" 2>&1) || echo "$d GRID_FAIL" >> "$O/fail.txt"
(cd "$W/b5/out-$d" 2>/dev/null && find . -type f -name 'rounds*.csv' -exec cp --parents -t "$O" {} +)
echo "$d syms_b1=$(echo "$syms"|grep -c .) grid_s=$(( $(date +%s)-t0 ))" > "$O/units.txt"
rm -rf "$W"
touch "$O/done"
