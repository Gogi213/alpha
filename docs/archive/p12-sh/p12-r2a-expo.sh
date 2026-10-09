#!/bin/bash
# П-12 R2 (TK-063): семьи A после волны A: равная экспозиция §6(2) (p12-r2-expo.py на a-b + базовые деревья s-v) -> vn-b -> portfolio-sim cap 0/3,
# без --drop (v171b) и с --drop TRUMP/TRX/BCH (v171c, В-210) -> /data/p12r2a/closes-van-*, closes-vand-*, expo.json, expo-v171c.json. Метка expo.done.
set -u
O=/data/p12r2a; T=/data/tk0113/tools; D=/data/tk065; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT
cd $O || exit 2; rm -f expo.done
ln -sfn /data/p12r2/s-v $O/s-v; ln -sfn $O/a-b $O/v-b
python3 $O/p12-r2-expo.py $O > expo.log 2>&1 || { echo FAILEXPO >> fail.txt; exit 1; }
EXPO_DROP=$DROP EXPO_OUT=$O/expo-v171c.json python3 $O/p12-r2-expo.py $O > expo-c.log 2>&1 || { echo FAILEXPOC >> fail.txt; exit 1; }
args=(); while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $D/days/r2a-2026-03-10.cells
for mo in $(ls vn-b | sort); do
  for cap in 0 3; do
    python3 $T/portfolio-sim.py --epoch "$mo=$O/vn-b:$mo" "${args[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-van-cap$cap-$mo.json > psim-van-cap$cap-$mo.txt 2> psim-van-cap$cap-$mo.log || echo "FAIL van $cap $mo" >> fail.txt
    python3 $T/portfolio-sim.py --epoch "$mo=$O/vn-b:$mo" "${args[@]}" --drop $DROP --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out closes-vand-cap$cap-$mo.json > psim-vand-cap$cap-$mo.txt 2> psim-vand-cap$cap-$mo.log || echo "FAIL vand $cap $mo" >> fail.txt
  done
done
touch expo.done
