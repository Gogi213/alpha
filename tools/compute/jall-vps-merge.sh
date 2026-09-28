#!/usr/bin/env bash
# TK-018 (дека): ворота VPS (4 клетки TK-010 на 07-31, *-vpsgate, против деки побайтно без строк `#`) → при ok
# перенос готовых суток VPS из e-jul/vps-in/<d>/b5/<клетка>/<d> в e-jul/b5/ (по мере прихода, пока все сутки VPS не здесь).
H=/home/deck/alpha/epochs/e-jul
T=/home/deck/alpha/tmp-p07
body() { grep -v '^#' "$1"; }
until [ -e $H/vps-in/vgate.done ]; do sleep 60; done
ok=1
for c in p07m-main p07a-base p07b-base p07a-h2-fr1; do
  for f in rounds.csv signals.csv; do
    a=$(find $H/vps-in/vgate -path "*/$c-vpsgate/2026-07-31/t-bid-btc4h-q1/$f" | head -1)
    b=$H/b5/$c/2026-07-31/t-bid-btc4h-q1/$f
    if [ -z "$a" ] || ! cmp -s <(body "$a") <(body "$b"); then ok=0; echo "$c/$f: РАЗНИЦА"; else echo "$c/$f: ok"; fi
  done
done > $T/jall-vgate.txt
[ $ok = 1 ] && echo "ВОРОТА VPS: ok" >> $T/jall-vgate.txt || { echo "ВОРОТА VPS: НЕ ПРОЙДЕНЫ" >> $T/jall-vgate.txt; exit 1; }
while :; do
  left=0
  for n in $(seq 13 31); do
    d=2026-07-$n
    [ -e $H/vps-in/$d.merged ] && continue
    if [ -e $H/vps-in/$d.done ]; then
      for src in $(find $H/vps-in/$d -mindepth 3 -maxdepth 3 -type d -path "*/b5/*/$d"); do
        cell=$(basename "$(dirname "$src")")
        mkdir -p $H/b5/$cell
        [ -e $H/b5/$cell/$d ] || mv "$src" $H/b5/$cell/$d
      done
      touch $H/vps-in/$d.merged
    else
      left=$((left+1))
    fi
  done
  echo "$(date -u +%FT%TZ) VPS не перевезено: $left" > $T/jall-vps-merge.txt
  [ $left = 0 ] && break
  sleep 120
done
