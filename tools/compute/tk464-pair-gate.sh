#!/bin/bash
# TK-464: гейт парного режима --pair ядра tk464-sim против tk461_d2a.one на построчных выводах того же ядра.
# Вход: события /data/tk0461/ev/<монета>-<ТФ>.csv.gz (первые 400), контроли /data/tk0463/out/<монета>-<ТФ>.ctrl-in.csv, ячейки out/cells.csv (28).
# bash tk464-pair-gate.sh [монеты] ; итог /data/tk0464/pair/PAIR.DONE и gate.log
set -u
COINS=${1:-TWTUSDT,PYTHUSDT,ONGUSDT}
B=/data/tk0463; O=/data/tk0464/pair; NEW=${NEW:-/data/tk0464/bin/tk464-sim}; PY=/data/tk421/venv/bin/python
SRC=/srv/alpha-sda/candles-1m/binance-um; FUND=/data/tk0455/fund; W=/data/tk0461
mkdir -p $O; rm -f $O/PAIR.DONE $O/gate.log
for c in ${COINS//,/ }; do for tf in 60 240 1440; do
  p=$O/$c-$tf; ev=$p.ev.csv; ct=$B/out/$c-$tf.ctrl-in.csv
  zcat $W/ev/$c-$tf.csv.gz | head -401 >$ev
  $NEW $SRC/$c.csv.gz $tf $ev $FUND $B/out/cells.csv $p.scan.csv --threads 1 2>/dev/null
  $NEW $SRC/$c.csv.gz $tf $ct $FUND $B/out/cells.csv $p.ctrl.csv --threads 1 2>/dev/null
  for th in 1 4; do
    /usr/bin/time -f "%e с" -o $p.t$th $NEW $SRC/$c.csv.gz $tf $ev $FUND $B/out/cells.csv $p.pair$th.csv --threads $th --pair $ct 2>/dev/null
    echo "$c-$tf thr$th: $($PY $B/../tk0464/tk464_pair_gate.py $p.scan.csv $p.ctrl.csv $p.pair$th.csv 2>&1 | tail -1) | $(cat $p.t$th) | строк $(wc -l <$p.pair$th.csv)" >>$O/gate.log
  done
  rm -f $p.scan.csv $p.ctrl.csv
done; done
echo "jobs $(wc -l <$O/gate.log) ok $(grep -c IDENTICAL_PAIR $O/gate.log)" >$O/PAIR.DONE
