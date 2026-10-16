#!/bin/bash
# TK-278 п.2 (остаток): 25 старых клеток М вне деревьев F1 TK-196 (expo-old-<мес>.tsv из tk278-expo.sh) — closes с монетой и m (§6(2) П-12) по ГОТОВЫМ деревьям их волн.
#   группа A (22: Г-87 pyfresh, Г-92 pyre, Г-93 pynw; Летопись set r2a) — /data/p12r2a/a-b;  группа B (3: Г-95 sw3–5; set r3a) — /data/p12r3a/a-b.
#   tk278-expo-old.sh [мес…]   env PM (параллельных месяцев, 3); вход /data/tk0278/expo/expo-old-<мес>.tsv (стратегия<TAB>форма), tk278-expo.py рядом.
# -> /data/tk0278/expo-old/closes-sym-<A|B>-cap0-<мес>.json, chk-<A|B>-<мес>.json (без монеты), expo-<A|B>-<мес>.json; сверка «те же сделки, что в готовых closes» — tk278-expo-old-cmp.py.
set -u
W=/data/tk0278/expo-old; E=/data/tk0278/expo; H=$(dirname "$(readlink -f "$0")"); T=/data/tk0113/tools; RAW=/data/p12r3a/s-b; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; P=${PM:-3}
MONS=${*:-"01 02 03 04 05 06 07 08 09"}
mkdir -p $W; cd $W || exit 2; rm -f old.done old-fail.txt
one() { mo=2026-$1
  for g in A B; do
    if [ $g = A ]; then tree=/data/p12r2a/a-b; re='-(pyfresh|pynw|pyre)'; else tree=/data/p12r3a/a-b; re='-14400-ttl1800$'; fi
    grep -E -e "$re" $E/expo-old-$mo.tsv > spec-$g-$mo.txt
    [ -s spec-$g-$mo.txt ] || continue
    rm -rf busy-$g-$mo xt-$g-$mo; mkdir busy-$g-$mo; ln -s $tree/$mo busy-$g-$mo/$mo
    python3 $H/tk278-expo.py $RAW busy-$g-$mo xt-$g-$mo spec-$g-$mo.txt > expo-$g-$mo.log 2>&1 || { echo "FAIL expo $g $mo" >> old-fail.txt; continue; }
    mv xt-$g-$mo/expo.json expo-$g-$mo.json
    args=(); while IFS=$'\t' read -r s f; do args+=(--variant "$s@$f=$s/$f"); done < spec-$g-$mo.txt
    python3 $T/portfolio-sim.py --epoch "$mo=$W/xt-$g-$mo:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos 0 \
      --closes-out chk-$g-$mo.json --closes-sym-out closes-sym-$g-cap0-$mo.json > psim-$g-$mo.txt 2> psim-$g-$mo.log || echo "FAIL psim $g $mo" >> old-fail.txt
  done; }
export -f one; export W E H T RAW DROP
echo $MONS | tr ' ' '\n' | xargs -P "$P" -I{} bash -c 'one {}'
[ -e old-fail.txt ] || touch old.done
