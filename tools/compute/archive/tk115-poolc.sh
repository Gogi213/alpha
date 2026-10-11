#!/usr/bin/env bash
# TK-115 режим c: на сутки — touches --r1-cols (новый бинарник) → R1-B1 (signals+rounds, как tk064-pool2.sh; даёт 60s-колонки)
# → клетки e106 (tk115-gen.py --only c) на том же кэше подходов → выход в /data/tk0115/pool/<метка>/{signals,r3c}/<сутки>/ → чистка.
#   tk115-poolc.sh <метка> <мес: jan..oct> <сутки>...      env: BIN=/opt/alpha-compute/bin/alpha-b26tk115r2 D=3 TP=4 GT=2
set -uo pipefail
lab=$1; MON=$2; shift 2
BIN=${BIN:-/opt/alpha-compute/bin/alpha-b26tk115r2}; TP=${TP:-4}; D=${D:-3}; GT=${GT:-2}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=/data/tk0115/pool/$lab
V=/data/tk044/final3/verdict.csv; DAYS=/data/tk065/days; GEN=/data/tk065/tk115-gen.py
mkdir -p "$O/signals" "$O/r3c" "$O/log" "$O/gen"; : > "$O/fail.txt"
export HOME=$H BIN E O TP GT V MON DAYS GEN ALPHA_SKIP_SAME=1
unit_touches() {  # $1 рабочее дерево суток, $2 сутки, $3 монета
  local W=$1 d=$2 s=$3
  nice -n 5 "$BIN" lob touches --root "$W/study/root-$d" --symbol "$s" --h3-mode notional --h3-usd 10000 --approach-bps 20 \
    --r1-cols --out "$W/study/approaches/D20/$d/touches-$s.csv" > "$O/log/$d-$s.touches.log" 2>&1 || echo "$d $s TOUCHES_FAIL" >> "$O/fail.txt"
}
export -f unit_touches
process_day() {
  local d=$1 W syms n t0 t1 t2 t3
  W=$O/w-$d; rm -rf "$W"; mkdir -p "$W/study/approaches/D20/$d" "$W/b5" "$W/bin"
  ln -s "$BIN" "$W/bin/alpha-tk044k1-new"
  for x in "$E"/* "$E"/.[!.]*; do b=$(basename "$x"); case $b in study|b5|b5-ref|b5-solo|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s "$(readlink -f "$x")" "$W/$b"; done
  for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*|approaches) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
  mkdir -p "$W/study/root-$d"; cp -rs "$(readlink -f "$E/study/root-$d")"/. "$W/study/root-$d"/
  syms=$(awk -F, -v d="$d" 'NR>1 && $2==d && $17=="пускаем"{print $1}' "$V" | sort -u)
  n=$(echo "$syms" | grep -c .); [ "$n" -gt 0 ] || { echo "$d NOSYMS" >> "$O/fail.txt"; rm -rf "$W"; return; }
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
    > "$O/log/$d.grid.log" 2>&1) || echo "$d GRID_FAIL" >> "$O/fail.txt"
  t2=$(date +%s)
  mkdir -p "$O/signals/$d"
  (cd "$W/b5/out-$d" 2>/dev/null && find . -type f \( -name 'signals*.csv' -o -name 'rounds*.csv' \) -exec cp --parents -t "$O/signals/$d" {} +)
  python3 "$GEN" "$DAYS/r2-$d.sh" "$d" "$O/gen" --only c > /dev/null 2>> "$O/fail.txt" || echo "$d GEN_FAIL" >> "$O/fail.txt"
  (cd "$W" && bash "$O/gen/r3c-$d.sh" > "$O/log/$d.r3c.log" 2>&1) || echo "$d R3C_FAIL" >> "$O/fail.txt"
  mkdir -p "$O/r3c/$d"; cp -r "$W/b5/r3c/$d"/. "$O/r3c/$d"/ 2>/dev/null || echo "$d R3C_NOOUT" >> "$O/fail.txt"
  t3=$(date +%s)
  echo "$d syms=$n touches_s=$((t1-t0)) grid_s=$((t2-t1)) r3c_s=$((t3-t2)) approaches_MB=$(du -sm "$W/study/approaches/D20/$d" | cut -f1) signals_files=$(find "$O/signals/$d" -type f | wc -l) r3c_files=$(find "$O/r3c/$d" -type f | wc -l)" >> "$O/units.txt"
  rm -rf "$W"
}
export -f process_day
printf '%s\n' "$@" | xargs -P "$D" -I{} bash -c 'process_day "$0"' {}
touch "$O/done"
