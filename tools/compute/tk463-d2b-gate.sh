#!/bin/bash
# TK-463: гейт D2b-режимов ядра (SS, TW, БУ, ОТ1/3/4/5) против эталона tk461_sim.trade(swing=,bar_end=,ot=) (tk463_d2b_ref.py).
# bash tk463-d2b-gate.sh [монеты через запятую] [макс. событий=300]; итог /data/tk0463/d2b/D2B.DONE: «jobs N ok M»
set -u
COINS=${1:-TWTUSDT,PYTHUSDT,ONGUSDT}; MAXEV=${2:-300}
W=/data/tk0461; B=/data/tk0463; OUT=$B/d2b; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um
BIN=$B/bin/tk463-sim; FUND=/data/tk0455/fund
mkdir -p "$OUT"; rm -f "$OUT/D2B.DONE" "$OUT/gate.log"
export PYTHONPATH=/data/tk0455:$W:/data/tk0463
cp /opt/rbuild/target-slot1/release/tk463-sim "$BIN" 2>/dev/null || true
$PY $B/tk463_d2b_ref.py cells $OUT/cells.csv
for c in ${COINS//,/ }; do for tf in 60 240 1440; do echo "$c $tf"; done; done > $OUT/plan.txt
job() { c=$1; tf=$2; p=$OUT/$c-$tf
  $PY $B/tk463_d2b_ref.py mk-in $W/ev/$c-$tf.csv.gz $p.in.csv $MAXEV
  $BIN $SRC/$c.csv.gz $tf $p.in.csv $FUND $OUT/cells.csv $p.rs.csv.gz --threads 1 2>>$OUT/err.log
  $PY $B/tk463_d2b_ref.py ref $SRC/$c.csv.gz $tf $p.in.csv $FUND $OUT/cells.csv $p.py.csv.gz 2>>$OUT/err.log
  r=$($PY $B/tk463_gate.py cmp $p.py.csv.gz $p.rs.csv.gz 2>&1 | tail -4 | tr '\n' ' ')
  echo "d2b $c $tf :: $r" >>$OUT/gate.log; }
export -f job; export OUT B BIN SRC W FUND PY MAXEV
xargs -P "${NX:-9}" -L1 bash -c 'job $0 $1' < $OUT/plan.txt
echo "jobs $(wc -l <$OUT/plan.txt) ok $(grep -c IDENTICAL $OUT/gate.log) lines $(wc -l <$OUT/gate.log)" > $OUT/D2B.DONE
