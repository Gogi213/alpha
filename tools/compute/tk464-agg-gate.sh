#!/bin/bash
# TK-464: гейт режима --agg ядра tk463-sim: (1) построчный вывод нового бинарника = прежнего (md5 распакованного),
# (2) агрегаты --agg 1 (1 и 4 потока) = агрегация построчного вывода (tk464_agg_gate.py). Входы — гейт D2b TK-463:
# /data/tk0463/d2b/<монета>-<ТФ>.in.csv + cells.csv (12 ячеек SS/TW/БУ/ОТ) и D2a: ev + out/cells.csv (28 ячеек), контроли out/<монета>-<ТФ>.ctrl-in.csv.
# bash tk464-agg-gate.sh [монеты] ; итог /data/tk0464/gate/AGG.DONE и gate.log
set -u
COINS=${1:-TWTUSDT,PYTHUSDT,ONGUSDT}
B=/data/tk0463; O=/data/tk0464/gate; OLD=$B/bin/tk463-sim; NEW=/data/tk0464/bin/tk464-sim; PY=/data/tk421/venv/bin/python
SRC=/srv/alpha-sda/candles-1m/binance-um; FUND=/data/tk0455/fund; W=/data/tk0461
mkdir -p $O; rm -f $O/AGG.DONE $O/gate.log
chk() { name=$1; tf=$2; c=$3; inp=$4; cells=$5; p=$O/$name
  $OLD $SRC/$c.csv.gz $tf $inp $FUND $cells $p.old.csv --threads 1 2>/dev/null
  $NEW $SRC/$c.csv.gz $tf $inp $FUND $cells $p.new.csv --threads 1 2>/dev/null
  $NEW $SRC/$c.csv.gz $tf $inp $FUND $cells $p.agg1.csv --threads 1 --agg 1 2>/dev/null
  $NEW $SRC/$c.csv.gz $tf $inp $FUND $cells $p.agg4.csv --threads 4 --agg 1 2>/dev/null
  m1=$(md5sum <$p.old.csv | cut -c1-8); m2=$(md5sum <$p.new.csv | cut -c1-8)
  r1=$($PY $B/../tk0464/tk464_agg_gate.py $p.new.csv $p.agg1.csv 2>&1 | tail -1); r4=$($PY $B/../tk0464/tk464_agg_gate.py $p.new.csv $p.agg4.csv 2>&1 | tail -1)
  echo "$name rows $m1 $m2 $([ $m1 = $m2 ] && echo BYTES_EQ || echo ROWS_DIFF) | agg1: $r1 | agg4: $r4 | строк $(wc -l <$p.new.csv) агр. $(wc -l <$p.agg1.csv)" >>$O/gate.log
  rm -f $p.old.csv $p.new.csv; }
for c in ${COINS//,/ }; do for tf in 60 240 1440; do
  chk d2b-$c-$tf $tf $c $B/d2b/$c-$tf.in.csv $B/d2b/cells.csv
  [ -f $B/out/$c-$tf.ctrl-in.csv ] && chk ctrl-$c-$tf $tf $c $B/out/$c-$tf.ctrl-in.csv $B/out/cells.csv
  zcat $W/ev/$c-$tf.csv.gz | head -401 > $O/ev-$c-$tf.csv
  chk d2a-$c-$tf $tf $c $O/ev-$c-$tf.csv $B/out/cells.csv
done; done
echo "jobs $(wc -l <$O/gate.log) eq $(grep -c BYTES_EQ $O/gate.log) agg_ok $(grep -c 'agg1: IDENTICAL_AGG.*agg4: IDENTICAL_AGG' $O/gate.log)" >$O/AGG.DONE
