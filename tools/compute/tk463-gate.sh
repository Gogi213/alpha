#!/bin/bash
# TK-463: гейт «число-в-число» ядра tk463-sim против эталонных /data/tk0461/{scan,ctrl}; свои выводы — /data/tk0463/out (эталон не трогаем).
# bash tk463-gate.sh [число монет=7] [первый номер в ls -S по ТФ 60 = 40]; метка /data/tk0463/out/GATE.DONE: «jobs N ok M»
set -u
NC=${1:-7}; FROM=${2:-40}
W=/data/tk0461; B=/data/tk0463; OUT=$B/out; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um
BIN=$B/bin/tk463-sim; FUND=/data/tk0455/fund
mkdir -p "$OUT"; rm -f "$OUT/GATE.DONE" "$OUT/gate.log"
export PYTHONPATH=/data/tk0455:$W:/data/tk0463
cp /opt/rbuild/target-slot1/release/tk463-sim "$BIN" 2>/dev/null || true
$PY $B/tk463_gate.py cells $OUT/cells.csv
COINS=$(ls -S $W/scan/*-60.csv.gz | sed -n "${FROM},$((FROM+NC-1))p" | xargs -n1 basename | sed 's/-60.csv.gz//')
for c in $COINS; do for tf in 60 240 1440; do echo "$c $tf"; done; done > $OUT/plan.txt
job() { c=$1; tf=$2; p=$OUT/$c-$tf
  s=$(date +%s.%N)
  $BIN $SRC/$c.csv.gz $tf $W/ev/$c-$tf.csv.gz $FUND $OUT/cells.csv $p.scan.csv.gz --threads 1 2>>$OUT/err.log
  e=$(date +%s.%N)
  r1=$($PY $B/tk463_gate.py cmp $W/scan/$c-$tf.csv.gz $p.scan.csv.gz 2>&1 | tail -3 | tr '\n' ' ')
  echo "scan $c $tf sec $(echo "$e - $s" | bc) :: $r1" >>$OUT/gate.log
  $PY $B/tk463_gate.py ctrl-entries $SRC/$c.csv.gz $tf $W/ev/$c-$tf.csv.gz $p.ctrl-in.csv
  s=$(date +%s.%N)
  $BIN $SRC/$c.csv.gz $tf $p.ctrl-in.csv $FUND $OUT/cells.csv $p.ctrl.csv.gz --threads 1 2>>$OUT/err.log
  e=$(date +%s.%N)
  r2=$($PY $B/tk463_gate.py cmp $W/ctrl/$c-$tf.csv.gz $p.ctrl.csv.gz 2>&1 | tail -3 | tr '\n' ' ')
  echo "ctrl $c $tf sec $(echo "$e - $s" | bc) :: $r2" >>$OUT/gate.log; }
export -f job; export OUT B BIN SRC W FUND PY
xargs -P "${NX:-7}" -L1 bash -c 'job $0 $1' < $OUT/plan.txt
echo "jobs $(wc -l <$OUT/plan.txt) ok $(grep -c IDENTICAL $OUT/gate.log) lines $(wc -l <$OUT/gate.log)" > $OUT/GATE.DONE
