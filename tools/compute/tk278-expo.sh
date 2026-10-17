#!/bin/bash
# TK-278 п.2: closes с монетой и равной экспозицией (§6(2) П-12, m в qty) для клеток М (Г-87/92/93/95), посчитанных в F1 TK-196 (деревья r-b), по ГОТОВЫМ деревьям.
# База m — сырые деревья B1 /data/p12r3a/s-b (как tk194). Клетки вне деревьев TK-196 (старые R2-A/R2-B) -> expo-old.tsv, не считаются здесь.
#   tk278-expo.sh [мес…]   env PM (параллельных месяцев, 3); вход tk278-expospec.tsv рядом (мес hyp хеш стратегия форма)
# -> /data/tk0278/expo/closes-sym-m-cap0-<мес>.json (+ chk-m-<мес>.json = closes без монеты), expo-<мес>/expo.json (m по клеткам), метка m.done.
set -u
W=/data/tk0278/expo; H=$(dirname "$(readlink -f "$0")"); T=/data/tk0113/tools; F1=/data/tk0196/psim; RAW=/data/p12r3a/s-b; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; P=${PM:-3}
MONS=${*:-"01 02 03 04 05 06 07 08 09"}
mkdir -p $W; cd $W || exit 2; rm -f m.done m-fail.txt
one() { mo=2026-$1
  d1=$(ls $F1/s-r/$mo | head -1); cells=/data/tk0196/out/$d1/cells.txt
  awk -F'\t' -v mo=$mo '$1==mo{print $4"\t"$5}' $H/tk278-expospec.tsv | sort -u > spec-$mo.all
  awk '{print $2"\t"$1}' $cells | sort -u | comm -12 - spec-$mo.all > spec-$mo.txt
  comm -13 <(sort spec-$mo.txt) spec-$mo.all > expo-old-$mo.tsv
  rm -rf busy-$mo xt-$mo; mkdir busy-$mo; ln -s $F1/r-b/$mo busy-$mo/$mo
  python3 $H/tk278-expo.py $RAW busy-$mo xt-$mo spec-$mo.txt > expo-$mo.log 2>&1 || { echo "FAIL expo $mo" >> m-fail.txt; return; }
  mkdir -p expo-$mo; mv xt-$mo/expo.json expo-$mo/expo.json
  args=(); while IFS=$'\t' read -r s f; do args+=(--variant "$s@$f=$s/$f"); done < spec-$mo.txt
  python3 $T/portfolio-sim.py --epoch "$mo=$W/xt-$mo:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos 0 \
    --closes-out chk-m-$mo.json --closes-sym-out closes-sym-m-cap0-$mo.json > psim-m-$mo.txt 2> psim-m-$mo.log || echo "FAIL psim $mo" >> m-fail.txt; }
export -f one; export W H T F1 RAW DROP
echo $MONS | tr ' ' '\n' | xargs -P "$P" -I{} bash -c 'one {}'
[ -e m-fail.txt ] || touch m.done
