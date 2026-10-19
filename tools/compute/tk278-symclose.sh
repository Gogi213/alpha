#!/bin/bash
# TK-278 п.1: closes с монетой ([мс, $, монета]) по ГОТОВЫМ деревьям — R1 (три набора), Э (TK-231), К (Г-117, TK-266). Бэктест не считается (В-213):
# те же деревья и ключи, что у исходных psim, cap 0, + --closes-sym-out; --closes-out для сверки с готовыми closes (tk278-cmp.py).
#   tk278-symclose.sh <набор> [мес…]      наборы: r1x r1col r1old e k   (по умолч. 9 мес. янв–сен); env PM (параллельных месяцев, 3)
# -> /data/tk0278/psim/closes-sym-<набор>-cap0-<мес>.json (+ chk-<набор>-<мес>.json), метка <набор>.done, сбой <набор>-fail.txt.
set -u
S=$1; shift; W=/data/tk0278/psim; T=/data/tk0113/tools; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; P=${PM:-3}
MONS=${*:-"01 02 03 04 05 06 07 08 09"}
mkdir -p $W; cd $W || exit 2; rm -f $S.done $S-fail.txt
psim() { # $1=мес $2=tree-root $3=aргументы…
  local mo=$1 tr=$2; shift 2
  python3 $T/portfolio-sim.py --epoch "2026-$mo=$tr" "$@" --deposit-usd 2500 --position-usd 500 --max-pos 0 \
    --closes-out chk-$S-$mo.json --closes-sym-out closes-sym-$S-cap0-$mo.json > psim-sym-$S-$mo.txt 2> psim-sym-$S-$mo.log || echo "FAIL $S $mo" >> $S-fail.txt; }
r1() { # $1=мес $2=O $3=каталог клеток
  local mo=$1 O=$2 C=$3 m; m=$(echo "jan feb mar apr may jun jul aug sep" | cut -d' ' -f$((10#$mo))); local form; form=$(cat $O/form-$m.txt); local args=(--variant "B1=B1/$form") c
  for f in $C/$m/*.csv; do c=$(basename $f .csv); grep -qx $c $O/empty-$m.txt || args+=(--variant "$c=$c/$form"); done
  psim $mo "$O/b:2026-$mo" "${args[@]}"; }
one() { mo=$1
  case $S in
    r1x)   r1 $mo /data/tk0251/ps /data/tk202/r1x/cells ;;
    r1col) r1 $mo /data/tk0251/p3/ps /data/tk0251/p3/cells ;;
    r1old) r1 $mo /data/tk063r1 /data/tk064/r1/cells ;;
    e) O=/data/tk0231/psim; d1=$(ls $O/s-r/2026-$mo | head -1); args=()
       while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $(ls -d /data/tk0231/out/run-*-$d1/)cells.txt
       psim $mo "$O/r-b:2026-$mo" "${args[@]}" --drop $DROP ;;
    k) args=(); while read f s; do args+=(--variant "$s@$f=$s/$f"); done < /data/tk065/days/r2t-2026-03-10.cells
       psim $mo "/data/p12r2/vtn-b:2026-$mo" "${args[@]}" --drop $DROP ;;
    *) echo "набор?" >> $S-fail.txt ;;
  esac; }
export -f one psim r1; export S W T DROP
echo $MONS | tr ' ' '\n' | xargs -P "$P" -I{} bash -c 'one {}'
[ -e $S-fail.txt ] || touch $S.done
