#!/usr/bin/env bash
# TK-064 шаг 4: R1 по пулу — на сутки: touches --r1-cols по монетам «пускаем» → подходы D20 с R1 в рабочее дерево суток →
# одна клетка B1 (bounce-grid --r1-cols, signals.csv с колонками R1) → signals в /data/tk064/pool/<метка>/signals/<сутки>/ → чистка.
#   tk064-pool.sh <метка> <мес: jan..oct> <сутки>...      env: BIN=/data/tk064/bin/alpha-tk064-r1  P=8
# РЕТРАЙ упавших touches (К1: verify-маркер не ok, а вердикт TK-044 = пускаем) с --allow-unverified: только монеты из fail.txt месяца → signals-retry/. v2 (06.10): голый юнит без замка; сутки параллельно: D суток × TP монет в touches (env D=3 TP=4), grid --threads GT=2.
set -uo pipefail
lab=$1; MON=$2; shift 2
BIN=${BIN:-/data/tk064/bin/alpha-tk064-r1}; TP=${TP:-4}; D=${D:-3}; GT=${GT:-2}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=/data/tk064/pool/$lab
V=/data/tk044/final3/verdict.csv
mkdir -p "$O/signals-retry" "$O/log-retry"; : > "$O/fail-retry.txt"
export HOME=$H BIN E O TP GT V MON
unit_touches() {  # $1 рабочее дерево суток, $2 сутки, $3 монета
  local W=$1 d=$2 s=$3
  nice -n 5 "$BIN" lob touches --root "$W/study/root-$d" --symbol "$s" --h3-mode notional --h3-usd 10000 --approach-bps 20 \
    --r1-cols --allow-unverified --out "$W/study/approaches/D20/$d/touches-$s.csv" > "$O/log-retry/$d-$s.touches.log" 2>&1 || echo "$d $s TOUCHES_FAIL" >> "$O/fail-retry.txt"
}
export -f unit_touches
process_day() {
  local d=$1 W syms n t0 t1 t2
  W=$O/wr-$d; rm -rf "$W"; mkdir -p "$W/study/approaches/D20/$d" "$W/b5"
  for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
  for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
  mkdir -p "$W/study/root-$d"; cp -rs "$(readlink -f "$E/study/root-$d")"/. "$W/study/root-$d"/
  syms=$(awk -v d="$d" '$1==d && $3=="TOUCHES_FAIL"{print $2}' "$O/fail.txt" | sort -u)
  n=$(echo "$syms" | grep -c .); [ "$n" -gt 0 ] || { echo "$d NOSYMS" >> "$O/fail-retry.txt"; rm -rf "$W"; return; }
  t0=$(date +%s)
  echo "$syms" | xargs -P "$TP" -I{} bash -c 'unit_touches "$0" "$1" "$2"' "$W" "$d" {}
  t1=$(date +%s)
  (cd "$W" && nice -n 5 "$BIN" lob bounce-grid --verdict-csv "$V" --root "study/root-$d" --touches-from study/approaches/D20 \
    --signal approach --queue-model prob:3 \
    --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
    --regime-from study/regime --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 \
    --entry-ttl-secs 1800 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide \
    --entry-form ladder3x0..0.0409sw2 --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 \
    --sigma-from study/sigma240 --set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55 --r1-cols --out-dir "b5/out-$d" \
    > "$O/log-retry/$d.grid.log" 2>&1) || echo "$d GRID_FAIL" >> "$O/fail-retry.txt"
  t2=$(date +%s)
  mkdir -p "$O/signals-retry/$d"
  (cd "$W/b5/out-$d" 2>/dev/null && find . -type f \( -name 'signals*.csv' -o -name 'rounds*.csv' \) -exec cp --parents -t "$O/signals-retry/$d" {} +)
  echo "$d syms=$n touches_s=$((t1-t0)) grid_s=$((t2-t1)) approaches_MB=$(du -sm "$W/study/approaches/D20/$d" | cut -f1) signals_files=$(find "$O/signals-retry/$d" -type f | wc -l)" >> "$O/units-retry.txt"
  rm -rf "$W"
}
export -f process_day
printf '%s\n' "$@" | xargs -P "$D" -I{} bash -c 'process_day "$0"' {}
touch "$O/done-retry"
