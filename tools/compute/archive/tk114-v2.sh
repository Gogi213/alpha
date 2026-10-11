#!/bin/bash
# TK-114 v2 (Судья 16:05, п.4–6): половины/полный пул на ДЕРЕВЕ ВОЛНЫ A (/data/p12r2a/vn-b, ячейки r2a-2026-03-10.cells); B1 — /data/p12r2/vn-b (как принятое).
# Наборы: A (все 26 клеток r2a, drop=dropB) · B (победитель, drop=dropA) · F (победитель+B1, drop=v171c) · G (победитель+B1, v171b без drop). Выход /data/tk0114/v2/<набор>/; метка v2/done.
set -u
O=/data/tk0114/v2; T=/data/tk0113/tools; D=/data/tk065; W=pynw3u3; DROP3=TRUMPUSDT,TRXUSDT,BCHUSDT
mkdir -p $O/A $O/B $O/F $O/G; cd $O || exit 2; rm -f done fail.txt
one() { set=$1; mo=$2; cap=$3; args=(); src=/data/p12r2a/vn-b
  case $set in
    A) drop=$(cat /data/tk0114/dropB.txt); while read f s; do args+=(--variant "$s@$f=$s/$f"); done < $D/days/r2a-2026-03-10.cells ;;
    B) drop=$(cat /data/tk0114/dropA.txt); while read f s; do case $f in *-$W) args+=(--variant "$s@$f=$s/$f");; esac; done < $D/days/r2a-2026-03-10.cells ;;
    F|G) [ $set = F ] && drop=$DROP3 || drop=; while read f s; do case $f in *-$W) args+=(--variant "$s@$f=$s/$f");; esac; done < $D/days/r2a-2026-03-10.cells ;;
  esac
  dr=(); [ -n "$drop" ] && dr=(--drop "$drop")
  python3 $T/portfolio-sim.py --epoch "$mo=$src:$mo" "${args[@]}" "${dr[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
    --closes-out $O/$set/closes-cap$cap-$mo.json --closes-sym-out $O/$set/closessym-cap$cap-$mo.json > $O/$set/psim-cap$cap-$mo.txt 2> $O/$set/psim-cap$cap-$mo.log || echo "FAIL $set $cap $mo" >> $O/fail.txt
  if [ $set = F ] || [ $set = G ]; then  # B1 из дерева p12r2 (vn-b), как в принятом
    b=(); while read f s; do case $f in *-pynw3u3|ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800) [ $s = t-bid-btc4h-q1 ] && b+=(--variant "$s@$f=$s/$f");; esac; done < $D/days/r2-2026-03-10.cells
    python3 $T/portfolio-sim.py --epoch "$mo=/data/p12r2/vn-b:$mo" "${b[@]}" "${dr[@]}" --deposit-usd 2500 --position-usd 500 --max-pos $cap \
      --closes-out $O/$set/b1-closes-cap$cap-$mo.json --closes-sym-out $O/$set/b1-closessym-cap$cap-$mo.json > $O/$set/b1-psim-cap$cap-$mo.txt 2> $O/$set/b1-psim-cap$cap-$mo.log || echo "FAIL b1 $set $cap $mo" >> $O/fail.txt
  fi; }
export -f one; export O T D W DROP3
for mo in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-06 2026-07 2026-08 2026-09; do for set in A B F G; do for cap in 0 3; do echo "$set $mo $cap"; done; done; done | xargs -P 6 -L1 bash -c 'one $0 $1 $2'
[ -f fail.txt ] || touch done
