#!/usr/bin/env bash
# TK-064 п.5: size_at_arm (= level_qty, лоты) для сигналов B1 пула — в signals.csv его нет, подходы D20 после чистки удалены.
#   tk064-lvl.sh <метка pool/m-..> <мес> <сутки>...   env: BIN D TP   выход: $O/lvl/<сутки>.csv = symbol,arm_ms,price_tick,size_at_arm (bid)
# Монеты — только те, что есть в signals.csv суток; touches без --r1-cols (флаг выкл. = старый формат, size_at_arm в нём).
set -uo pipefail
lab=$1; MON=$2; shift 2
BIN=${BIN:-/data/tk064/bin/alpha-tk064-r1}; TP=${TP:-4}; D=${D:-3}
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; O=/data/tk064/pool/$lab
mkdir -p "$O/lvl" "$O/lvllog"; : > "$O/lvl-fail.txt"
export HOME=$H BIN E O TP
unit_touches() {
  local W=$1 d=$2 s=$3
  nice -n 5 "$BIN" lob touches --root "$W/study/root-$d" --symbol "$s" --h3-mode notional --h3-usd 10000 --approach-bps 20 \
    --out "$W/study/approaches/D20/$d/touches-$s.csv" > "$O/lvllog/$d-$s.log" 2>&1 || echo "$d $s TOUCHES_FAIL" >> "$O/lvl-fail.txt"
}
export -f unit_touches
process_day() {
  local d=$1 W syms f
  f=$O/signals/$d/t-bid-btc4h-q1/signals.csv
  [ -f "$f" ] || f=$(ls "$O"/signals-retry/$d/t-bid-btc4h-q1/signals.csv 2>/dev/null)
  [ -f "$f" ] || { echo "$d NOSIGNALS" >> "$O/lvl-fail.txt"; return; }
  syms=$(grep -v '^#' "$f" | awk -F, 'NR>1{print $1}' | sort -u)
  if [ -z "$syms" ]; then printf 'symbol,arm_ms,price_tick,size_at_arm\n' > "$O/lvl/$d.csv"; return; fi
  W=$O/lw-$d; rm -rf "$W"; mkdir -p "$W/study/approaches/D20/$d"
  mkdir -p "$W/study/root-$d"; cp -rs "$(readlink -f "$E/study/root-$d")"/. "$W/study/root-$d"/
  echo "$syms" | xargs -P "$TP" -I{} bash -c 'unit_touches "$0" "$1" "$2"' "$W" "$d" {}
  { printf 'symbol,arm_ms,price_tick,size_at_arm\n'
    for g in "$W"/study/approaches/D20/$d/touches-*.csv; do
      s=$(basename "$g" .csv); s=${s#touches-}
      grep -v '^#' "$g" | awk -F, -v s="$s" 'NR==1{for(i=1;i<=NF;i++)c[$i]=i;next} $c["side"]=="bid"{print s","$c["arm_ms"]","$c["price_tick"]","$c["size_at_arm"]}'
    done; } > "$O/lvl/$d.csv"
  rm -rf "$W"
}
export -f process_day
printf '%s\n' "$@" | xargs -P "$D" -I{} bash -c 'process_day "$0"' {}
touch "$O/lvl-done"
