#!/usr/bin/env bash
# TK-360: проход валидности СПОТА (как tk345-validity-pass.sh для фьюча): те же 12 наборов фильтров и семейства форм F1–F3,
# (ret1h* убраны: в --signal approach ключи ret* не определены — отказ движка; 10 наборов) но корень/подходы спота: /data/tk0319/e-<мес>/study/root-<сутки>, подходы журнала /data/tk0319/jrn/<сутки>/wt/sub/D20 (--signal approach).
#   tk360-validity-spot.sh <сутки> <выход-каталог> "<символы через пробел>"      env: BIN GT FAMS ("F1 F2 F3") TAG (метка единицы: <сутки>-<TAG>-Fn, по умолчанию без метки)
set -uo pipefail
d=$1; O=$2; SYMS=$3
BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk0289c-pgo}; GT=${GT:-1}
mon=$(case ${d:5:2} in 01) echo jan;; 02) echo feb;; 03) echo mar;; 04) echo apr;; 05) echo may;; 06) echo jun;; 07) echo jul;; 08) echo aug;; 09) echo sep;; esac)
E=/data/tk0319/e-$mon; JRN=/data/tk0319/jrn; RG=/data/tk0196/regime
[ -e "$JRN/$d/done" ] || { echo "$d нет журнала $JRN/$d" >&2; exit 4; }
W=$O/w-$d${TAG:+-$TAG}; rm -rf "$W"; mkdir -p "$W/study" "$O"
ln -s "$JRN/$d/wt" "$W/wt"
for x in "$E"/*; do b=$(basename "$x"); [ "$b" = study ] || ln -s "$(readlink -f "$x")" "$W/$b"; done
for x in "$E"/study/*; do b=$(basename "$x"); case $b in root-*) continue;; esac; ln -s "$(readlink -f "$x")" "$W/study/$b"; done
ln -s "$(readlink -f "$E/study/root-$d")" "$W/study/root-$d"; ln -s "$RG/$mon" "$W/study/regime"
common=(lob bounce-grid --root "study/root-$d" --day "$d" --touches-from wt/sub/D20 --signal approach --regime-from study/regime
  --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000
  --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide
  --set base:side=bid --set age10:side=bid,age=10 --set age60:side=bid,age=60 --set usd30k:side=bid,usd_min=30000
  --set flow100:side=bid,flow=100 --set fr:side=bid,frontrun --set btcup:side=bid,btc4h_min=0 --set btcdn:side=bid,btc4h_max=0
  --set age10usd30k:side=bid,age=10,usd_min=30000 --set ask:side=ask)
F1=(--entry-form single@fr --entry-form market --stop-form before --stop-form pct0.5 --stop-form pct2 --take-form 1to1 --take-form tr1x1 --deadline-secs 1800 --exit-form none --early-exit-secs off)
F2=(--entry-form single@fr --stop-form pct2 --take-form 1to1 --deadline-secs 1800 --deadline-secs 14400 --exit-form none --exit-form eat50 --exit-form gone50 --early-exit-secs 2 --early-exit-secs off)
F3=(--entry-form market --stop-form pct0.5 --take-form 1to1 --take-form tr1x1 --deadline-secs 14400 --exit-form eat50 --exit-form gone50 --early-exit-secs 2 --early-exit-secs off)
sargs=(); for s in $SYMS; do sargs+=(--symbol "$s"); done
rcall=0
for fam in ${FAMS:-F1 F2 F3}; do
  u="$O/$d${TAG:+-$TAG}-$fam"; [ -f "$u/done" ] && continue; rm -rf "$u"; mkdir -p "$u"
  case $fam in F1) fa=("${F1[@]}");; F2) fa=("${F2[@]}");; F3) fa=("${F3[@]}");; esac
  t1=$(date +%s)
  (cd "$W" && nice -n 5 "$BIN" "${common[@]}" "${fa[@]}" "${sargs[@]}" --out-dir "$u/out") > "$u/grid.log" 2>&1
  rc=$?; echo "$d $fam rc=$rc s=$(( $(date +%s)-t1 )) $SYMS" >> "$O/units.txt"
  if [ $rc -eq 0 ]; then touch "$u/done"; else echo "rc=$rc" > "$u/fail"; rcall=1; fi
done
rm -rf "$W"; exit $rcall
