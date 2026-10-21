#!/usr/bin/env bash
# TK-291 (П-19 §6): проход B1 (ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800, набор t-bid-btc4h-q1) 9 мес. × все сутки со столбцом
#   touch_index в rounds.csv (--touch-col; старые столбцы — байт в байт, гейт). Раннер — копия day-cells.sh (TK-219) + --touch-col.
#   tk291-b1-touch.sh prep   — каталог /data/tk291: раннер, план (одна клетка, пустые помесячные); сутки — /data/tk0231/days.txt
#   tk291-b1-touch.sh run    — проход (NX исполнителей по GT ядер); выход /data/tk291/out/run-<мес>-<сутки>/out/<сутки>/
set -u
D=/data/tk291; P=$D/plan
case ${1:-} in
prep)
  mkdir -p "$P"; S=/data/tk0231
  sed 's/--r1-cols /--r1-cols --touch-col /' $S/day-cells.sh > $D/day-cells.sh
  cp $S/manifest.py $S/merge-packs.py $D/
  echo 'ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800 t-bid-btc4h-q1' > $P/b1.cells
  printf '%s\n' '--deadline-secs 14400' '--entry-form ladder3x0..0.0409sw2' '--entry-ttl-secs 1800' '--stop-form pct2' '--take-form tr1x1' \
    '--set t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55' > $P/b1.args
  for m in 01 02 03 04 05 06 07 08 09; do : > $P/b1-2026-$m.cells; : > $P/b1-2026-$m.args; done
  grep -c touch-col $D/day-cells.sh;;
run)
  export PLAN=b1 P=$P O=$D/out BIN=$D/alpha-new GT=${GT:-1} TP=1 REASON="tk291: B1 со столбцом touch_index, бинарь не pgo (одна клетка)"
  xargs -P "${NX:-4}" -L1 bash -c 'bash /data/tk291/day-cells.sh "run-$0-$1" "$0" "$1"' < /data/tk0231/days.txt
  touch $D/b1.done;;
esac
