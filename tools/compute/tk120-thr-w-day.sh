#!/usr/bin/env bash
# TK-120 пороги Q для окон W=15/60 (e112/e116): копия tk115-w-day.sh с окном журнала ленты TL (--tape-log) и своим выходом; лоты tape_press_lots/cxl_lots
# в rounds.csv — за TL с до исполнения входа (как v5 для 30). Те же сделки B1, тот же бинарник, что у замера W.
#   tk120-thr-w-day.sh <мес> <сутки>   env: TL (обязательно) BIN GT=2 CHASE_MS=86400000   → /data/p12r3a/w<TL>/<сутки>/…, маркер done
set -uo pipefail
MON=$1; d=$2; TL=${TL:?TL}
BIN=${BIN:-/opt/alpha-compute/bin/alpha-b26tk115r2}; GT=${GT:-2}; CHASE_MS=${CHASE_MS:-86400000}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=/data/p12r3a/w$TL/$d; V=/data/tk044/final3/verdict.csv
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
  --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --tape-log $TL --out-dir "b5/out-$d" > "$O/grid.log" 2>&1) || echo "$d GRID_FAIL" >> "$O/fail.txt"
(cd "$W/b5/out-$d" 2>/dev/null && find . -type f -name 'rounds*.csv' -exec cp --parents -t "$O" {} +)
echo "$d syms_b1=$(echo "$syms"|grep -c .) grid_s=$(( $(date +%s)-t0 ))" > "$O/units.txt"
rm -rf "$W"
touch "$O/done"
