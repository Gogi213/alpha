#!/usr/bin/env bash
# TK-345 проход валидности: сутки × шард символов (каждый SH-й по алфавиту, SH=6) × группы по 3 символа × 3 семейства форм × 12 наборов фильтров.
# Семейства покрывают каждое значение каждой оси (не декартово произведение — оно в калибровке, 4 символа × сутки, 144 формы):
#   F1: вход {single@fr, market} × стоп {before, pct0.5, pct2} × тейк {1to1, tr1x1}, дедлайн 1800, выход none, early off  (12 форм)
#   F2: вход single@fr, стоп pct2, тейк 1to1 × дедлайн {1800, 14400} × выход {none, eat50, gone50} × early {2, off}      (12 форм)
#   F3: вход market, стоп pct0.5, тейк {1to1, tr1x1}, дедлайн 14400 × выход {eat50, gone50} × early {2, off}              (8 форм)
# Наборы age10/age60 вместо age1800/7200: в кэше касаний возраст стены ≤ ~13 мин (калибровка 01.02: age1800 → 0 сигналов).
#   tk345-validity-pass.sh <сутки> <шард 0..SH-1> <выход-каталог>        env: BIN GT SH GRP (символов в задаче, 3) FAMS ("F1 F2 F3") PROBE_SYMS (список символов вместо шарда)
set -uo pipefail
d=$1; sh=$2; O=$3
BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk0289c-pgo}; GT=${GT:-1}
mon=$(case ${d:5:2} in 01) echo jan;; 02) echo feb;; 03) echo mar;; 04) echo apr;; 05) echo may;; 06) echo jun;; 07) echo jul;; 08) echo aug;; 09) echo sep;; esac)
H=/data/tk046/$mon/home; E=$H/alpha/epochs/e-$mon
export HOME=$H
cd "$E" || exit 3
mapfile -t ALL < <(ls "study/root-$d"/*.binlog | sed "s#.*/##;s#-$d.binlog##" | sort)
SYMS=(); for i in "${!ALL[@]}"; do [ $((i % ${SH:-6})) -eq "$sh" ] && SYMS+=("${ALL[$i]}"); done
[ -n "${PROBE_SYMS:-}" ] && read -ra SYMS <<< "$PROBE_SYMS"
GRP=${GRP:-3}
mkdir -p "$O"
common=(lob bounce-grid --root "study/root-$d" --day "$d" --touches-from study/approaches/D20 --regime-from study/regime
  --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000
  --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide
  --set base:side=bid --set age10:side=bid,age=10 --set age60:side=bid,age=60 --set usd30k:side=bid,usd_min=30000
  --set flow100:side=bid,flow=100 --set fr:side=bid,frontrun --set btcup:side=bid,btc4h_min=0 --set btcdn:side=bid,btc4h_max=0
  --set ret1hup:side=bid,ret1h_min=0 --set ret1hdn:side=bid,ret1h_max=0 --set age10usd30k:side=bid,age=10,usd_min=30000 --set ask:side=ask)
F1=(--entry-form single@fr --entry-form market --stop-form before --stop-form pct0.5 --stop-form pct2 --take-form 1to1 --take-form tr1x1
  --deadline-secs 1800 --exit-form none --early-exit-secs off)
F2=(--entry-form single@fr --stop-form pct2 --take-form 1to1 --deadline-secs 1800 --deadline-secs 14400
  --exit-form none --exit-form eat50 --exit-form gone50 --early-exit-secs 2 --early-exit-secs off)
F3=(--entry-form market --stop-form pct0.5 --take-form 1to1 --take-form tr1x1 --deadline-secs 14400
  --exit-form eat50 --exit-form gone50 --early-exit-secs 2 --early-exit-secs off)
n=0; rcall=0
for ((i = 0; i < ${#SYMS[@]}; i += GRP)); do
  sargs=(); for s in "${SYMS[@]:i:GRP}"; do sargs+=(--symbol "$s"); done
  for fam in ${FAMS:-F1 F2 F3}; do
    u="$O/$d-s$sh-g$n-$fam"
    [ -f "$u/done" ] && continue
    rm -rf "$u"; mkdir -p "$u"
    case $fam in F1) fa=("${F1[@]}");; F2) fa=("${F2[@]}");; F3) fa=("${F3[@]}");; esac
    t1=$(date +%s)
    nice -n 5 "$BIN" "${common[@]}" "${fa[@]}" "${sargs[@]}" --out-dir "$u/out" > "$u/grid.log" 2>&1
    rc=$?; echo "$d s$sh g$n $fam rc=$rc s=$(( $(date +%s)-t1 )) ${SYMS[*]:i:GRP}" >> "$O/units.txt"
    if [ $rc -eq 0 ]; then touch "$u/done"; else echo "rc=$rc" > "$u/fail"; rcall=1; fi
  done
  n=$((n+1))
done
exit $rcall
