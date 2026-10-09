#!/bin/bash
# TK-120: равная экспозиция §6(2) для g95-sw3 + срабатывания e114 f1/f3 по busy-replay'нутым деревьям волны a (/data/p12r3a/a-b) и базе s-v (/data/p12r2/s-v)
# -> vn-b -> portfolio-sim cap 0/3, без --drop и с --drop (В-210) -> /data/p12r3a/closes-r3n[d]-cap<N>-<мес>.json, expo.json, expo-v171c.json. Метка expo2.done.
set -u
O=/data/p12r3a; T=/data/tk0113/tools; D=/data/tk065; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT; S=t-bid-btc4h-q1
B=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800
cd $O || exit 2; rm -f expo2.done
ln -sfn /data/p12r2/s-v $O/s-v; ln -sfn $O/a-b $O/v-b
python3 /data/tk0113/p12-r3-expo.py $O > expo.log 2>&1 || { echo FAILEXPO >> fail.txt; exit 1; }
EXPO_DROP=$DROP EXPO_OUT=$O/expo-v171c.json python3 /data/tk0113/p12-r3-expo.py $O > expo-c.log 2>&1 || { echo FAILEXPOC >> fail.txt; exit 1; }
args=()
for f in $B-halfstopf1 $B-halfstopf3 $B-halflevelf1 $B-halflevelf3 ladder3x0..0.0409sw3-pct2-tr1x1-14400-ttl1800; do args+=(--variant "$S@$f=$S/$f"); done
for mo in $(ls vn-b | sort); do
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/vn-b:$mo" "${args[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-r3n-cap$cap-$mo.json > psim-r3n-cap$cap-$mo.txt 2> psim-r3n-cap$cap-$mo.log || echo "FAIL r3n $cap $mo" >> fail.txt
    python3 $T/portfolio-sim.py --epoch "$mo=$O/vn-b:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-r3nd-cap$cap-$mo.json > psim-r3nd-cap$cap-$mo.txt 2> psim-r3nd-cap$cap-$mo.log || echo "FAIL r3nd $cap $mo" >> fail.txt
  done
done
touch expo2.done
