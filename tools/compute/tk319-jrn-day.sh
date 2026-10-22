#!/usr/bin/env bash
# TK-319 (П-21): журнал подходов D20 на споте — `lob touches --r1-cols --wall-log` по символам суток; команда дословно из
# tk115-jrn-day.sh (там фьючерс), корни — спот (/data/tk0319/e-<мес>, verdict.csv, b1-symdays.csv). Сетку не считает.
#   tk319-jrn-day.sh <мес> <сутки>   env: BIN TP=4 K=/data/tk0319 JRN=/data/tk0319/jrn SYMS=<список через пробел вместо symdays>
# Выход: $JRN/<сутки>/wt/sub/D20/<сутки>/touches-<SYM>.csv (+ approaches-/mids1m- рядом), маркер $JRN/<сутки>/done.
set -uo pipefail
MON=$1; d=$2
K=${K:-/data/tk0319}; BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk312-pgo}; TP=${TP:-4}; JRN=${JRN:-$K/jrn}
O=$JRN/$d; E=$K/e-$MON
mkdir -p "$O"; if [ -e "$O/done" ] || ! mkdir "$O/claim" 2>/dev/null; then exit 0; fi
mkdir -p "$O/log" "$O/wt/sub/D20/$d"; : > "$O/fail.txt"
syms=${SYMS:-$(awk -F, -v m="$MON" -v d="$d" 'NR>1 && $1==m && $2==d{print $3}' "$K/b1-symdays.csv" | sort -u)}
t0=$(date +%s)
for s in $syms; do echo "$s"; done | xargs -P "$TP" -I{} bash -c 'nice -n 5 "$0" lob touches --root "$1/study/root-$2" --symbol {} --h3-mode notional --h3-usd 10000 --approach-bps 20 --r1-cols --wall-log --out "$3/wt/sub/D20/$2/touches-{}.csv" > "$3/log/$2-{}.sub.log" 2>&1 || echo "$2 {} TOUCHES_FAIL" >> "$3/fail.txt"' "$BIN" "$E" "$d" "$O"
echo "$d syms=$(echo $syms | wc -w) touches_s=$(( $(date +%s) - t0 ))" >> "$JRN/units.txt"
touch "$O/done"
