#!/bin/bash
# TK-114 П-13 (Судья 15:40): отбор на половине А монет (sha256, docs/findings/p13-split-2026-10-09.json): portfolio-sim --drop = v171c + половина Б,
# cap 0/3 заново, янв–сен. Три набора: t1 (B1+pct3+pct4, /data/tk0114/on), ext (8 форм, /data/tk083/on), r2 (vn-b, клетки r2-2026-03-10.cells).
# Выход /data/tk0114/A/{t1,ext,r2}/closes[sym]-cap<0|3>-<мес>.json; метка /data/tk0114/A/done (fail.txt при сбое). Параметр: файл drop (по умолчанию dropB.txt).
set -u
DROPF=${1:-/data/tk0114/dropB.txt}; O=${2:-/data/tk0114/A}; T=/data/tk0113/tools; D=/data/tk065; X=/data/tk083
DROP=$(cat $DROPF); mkdir -p $O/t1 $O/ext $O/r2; cd $O || exit 2; rm -f done fail.txt
one() { mo=$1; set=$2; cap=$3
  case $set in
    t1)  src=/data/tk0114/on; args=(); for f in $(cat /data/tk0114/forms.txt); do args+=(--variant "$f=t-bid-btc4h-q1/$f"); done ;;
    ext) src=$X/on; args=(); for f in $(cat $X/forms.txt); do args+=(--variant "$f=t-bid-btc4h-q1/$f"); done ;;
    r2)  src=/data/p12r2/vn-b; args=(); while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $D/days/r2-2026-03-10.cells ;;
  esac
  [ -d $src/$mo ] || return 0
  python3 $T/portfolio-sim.py --epoch "$mo=$src:$mo" "${args[@]}" --drop "$DROP" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
    --closes-out $O/$set/closes-cap$cap-$mo.json --closes-sym-out $O/$set/closessym-cap$cap-$mo.json > $O/$set/psim-cap$cap-$mo.txt 2> $O/$set/psim-cap$cap-$mo.log || echo "FAIL $set $cap $mo" >> $O/fail.txt; }
export -f one; export O T D X DROP
for mo in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09; do for set in t1 ext r2; do for cap in 0 3; do echo "$mo $set $cap"; done; done; done \
  | xargs -P 4 -L1 bash -c 'one $0 $1 $2'
[ -f fail.txt ] || touch done
