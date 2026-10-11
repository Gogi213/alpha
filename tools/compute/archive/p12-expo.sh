#!/bin/bash
# П-12 (TK-135/С-64): равная экспозиция §6(2) + срабатывания §8: p12-r2-expo.py -> vn-b -> portfolio-sim (cap 0 и 3). Бывшие p12-r2-expo.sh / p12-r2a-expo.sh / p12-r3a-expo.sh — один скрипт, набор — аргумент:
#   r2  — /data/p12r2: vn-b/vtn-b -> closes-vn-*/closes-vtn-*; метка expo.done
#   r2a — /data/p12r2a: семьи A после волны A, без --drop (v171b) и с --drop TRUMP/TRX/BCH (В-210) -> closes-van-*, closes-vand-*, expo.json, expo-v171c.json; метка expo.done
#   r3a — TK-120: g95-sw3 (--g95) + e114 f1/f3 по busy-replay'нутым деревьям волны a и базе s-v -> closes-r3n[d]-*, expo.json, expo-v171c.json; метка expo2.done
# Запуск: p12-expo.sh <набор> (юнитом alsched).
set -u
D=/data/tk065; DROP=TRUMPUSDT,TRXUSDT,BCHUSDT
case ${1:?набор: r2|r2a|r3a} in
  r2)  O=/data/p12r2;  T=/data/tk083/tools;  EXPO=/data/p12r2/p12-r2-expo.py;  DONE=expo.done ;;
  r2a) O=/data/p12r2a; T=/data/tk0113/tools; EXPO=/data/p12r2a/p12-r2-expo.py; DONE=expo.done ;;
  r3a) O=/data/p12r3a; T=/data/tk0113/tools; EXPO=/data/tk0113/p12-r2-expo.py; DONE=expo2.done ;;
  *) echo "набор: r2|r2a|r3a" >&2; exit 2 ;;
esac
SET=$1; XA=(); [ $SET = r3a ] && XA=(--g95)
variants() {  # variants <вид> -> args=(--variant ...)
  args=()
  case $SET in
    r3a) S=t-bid-btc4h-q1; B=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800
         for f in $B-halfstopf1 $B-halfstopf3 $B-halflevelf1 $B-halflevelf3 ladder3x0..0.0409sw3-pct2-tr1x1-14400-ttl1800; do args+=(--variant "$S@$f=$S/$f"); done ;;
    *)   cells=$D/days/r2-2026-03-10.cells; [ $SET = r2 ] && [ $1 = vtn ] && cells=$D/days/r2t-2026-03-10.cells; [ $SET = r2a ] && cells=$D/days/r2a-2026-03-10.cells
         while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $cells ;;
  esac; }
cd $O || exit 2; rm -f $DONE
if [ $SET != r2 ]; then ln -sfn /data/p12r2/s-v $O/s-v; ln -sfn $O/a-b $O/v-b; fi
python3 $EXPO $O "${XA[@]}" > expo.log 2>&1 || { echo FAILEXPO >> fail.txt; exit 1; }
if [ $SET != r2 ]; then EXPO_DROP=$DROP EXPO_OUT=$O/expo-v171c.json python3 $EXPO $O "${XA[@]}" > expo-c.log 2>&1 || { echo FAILEXPOC >> fail.txt; exit 1; }; fi
KINDS="vn vtn"; [ $SET != r2 ] && KINDS=vn
for mo in $(ls vn-b | sort); do
  for kind in $KINDS; do
    [ -d $kind-b/$mo ] || continue
    variants $kind
    for cap in 0 3; do
      for d in "" d; do
        [ -n "$d" ] && [ $SET = r2 ] && continue   # --drop — только у r2a/r3a (v171c, В-210)
        case $SET in r2) PS=$kind CS=$kind FL="$kind " ;; r2a) PS=van$d CS=van$d FL="van$d " ;; r3a) PS=r3n$d CS=r3n$d FL="r3n$d " ;; esac
        DR=(); [ -n "$d" ] && DR=(--drop $DROP)
        JS=(); [ $SET = r2 ] && JS=(--json psim-$PS-cap$cap-$mo.json)
        python3 $T/portfolio-sim.py --epoch "$mo=$O/$kind-b:$mo" "${args[@]}" "${DR[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap "${JS[@]}" \
          --closes-out closes-$CS-cap$cap-$mo.json > psim-$PS-cap$cap-$mo.txt 2> psim-$PS-cap$cap-$mo.log || echo "FAIL $FL$cap $mo" >> fail.txt
      done
    done
  done
done
touch $DONE
