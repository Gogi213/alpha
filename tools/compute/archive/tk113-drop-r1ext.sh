#!/bin/bash
# TK-113/В-210: R1 (154 кл., /data/tk063r1/b) и ext (7 кл., /data/tk083/on) по пулу v171c: portfolio-sim --drop на готовых деревьях, cap 0 и 3.
# Выход /data/tk0113/drop2/{r1,ext}/closes-cap{0,3}-<мес>.json; метка /data/tk0113/drop2/done (fail.txt при сбое). Бэктест не пересчитывается.
set -u
O=/data/tk0113/drop2; T=/data/tk0113/tools; R=/data/tk063r1; C=/data/tk064/r1/cells; X=/data/tk083
mkdir -p $O/r1 $O/ext; cd $O || exit 2; rm -f done fail.txt
DROP=TRUMPUSDT,TRXUSDT,BCHUSDT
one_r1() { mon=$1; label=$2
  form=$(cat $R/form-$mon.txt); args=(--variant "B1=B1/$form")
  for cf in $C/$mon/*.csv; do c=$(basename $cf .csv); grep -qx "$c" $R/empty-$mon.txt && continue; args+=(--variant "$c=$c/$form"); done
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$label=$R/b:$label" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out $O/r1/closes-cap$cap-$mon.json > $O/r1/psim-cap$cap-$mon.txt 2> $O/r1/psim-cap$cap-$mon.log || echo "FAIL r1 $cap $mon" >> $O/fail.txt
  done; }
one_ext() { m=$1
  args=(); for f in $(cat $X/forms.txt); do args+=(--variant "$f=t-bid-btc4h-q1/$f"); done
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$m=$X/on:$m" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out $O/ext/closes-cap$cap-$m.json > $O/ext/psim-cap$cap-$m.txt 2> $O/ext/psim-cap$cap-$m.log || echo "FAIL ext $cap $m" >> $O/fail.txt
  done; }
export -f one_r1 one_ext; export O T R C X DROP
printf '%s\n' feb:2026-02 mar:2026-03 apr:2026-04 may:2026-05 jun:2026-06 jul:2026-07 aug:2026-08 sep:2026-09 oct:2026-10 | \
  xargs -P4 -I{} bash -c 'IFS=: read a b <<< "{}"; one_r1 $a $b; one_ext $b'
[ -f fail.txt ] || touch done
