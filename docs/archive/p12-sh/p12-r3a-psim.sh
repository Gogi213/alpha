#!/bin/bash
# TK-120: сделки волны a TK-115 (e114 f=1/4,3/4; g95 sw3) по v171c, янв–сен: деревья -> по месяцу -> busy-replay -> portfolio-sim (cap 0 и 3),
# без --drop (v171b-ряд) и с --drop TRUMP/TRX/BCH (v171c, В-210) -> /data/p12r3a/closes-r3[d]-cap<N>-<мес>.json. Без пересчёта волны (В-213).
# Равная экспозиция g95 (m = min(1, N_ref/N_peak), П-12 §6(2)) — следующим шагом (p12-r2-expo.py знает только суффиксные семьи). Метка psim2.done.
set -u
O=/data/p12r3a; T=/data/tk0113/tools; TB=/data/tk083/tools; D=/data/tk065; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; S=t-bid-btc4h-q1
B=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800
mkdir -p $O; cd $O || exit 2; rm -f psim.done psim2.done fail.txt
while read d m; do
  mo=${d:0:7}
  mkdir -p s-a/$mo
  [ -e s-a/$mo/$d ] || cp -rs $D/t15a-$d/b5/r3a/$d s-a/$mo/
done < $D/days/days.tsv
args=()
for f in $B-halfstopf1 $B-halfstopf3 $B-halflevelf1 $B-halflevelf3 ladder3x0..0.0409sw3-pct2-tr1x1-14400-ttl1800; do args+=(--variant "$S@$f=$S/$f"); done
for mo in $(ls s-a | sort); do
  [ -e a-b/$mo/busy-replay.txt ] || python3 $TB/busy-replay.py s-a/$mo a-b/$mo > busy-a-$mo.log 2>&1 || { echo "FAILBUSY $mo" >> fail.txt; continue; }
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/a-b:$mo" "${args[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-r3-cap$cap-$mo.json > psim-r3-cap$cap-$mo.txt 2> psim-r3-cap$cap-$mo.log || echo "FAIL r3 $cap $mo" >> fail.txt
    python3 $T/portfolio-sim.py --epoch "$mo=$O/a-b:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-r3d-cap$cap-$mo.json > psim-r3d-cap$cap-$mo.txt 2> psim-r3d-cap$cap-$mo.log || echo "FAIL r3d $cap $mo" >> fail.txt
  done
done
touch psim2.done
