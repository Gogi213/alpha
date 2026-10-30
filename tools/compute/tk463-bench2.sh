#!/bin/bash
# TK-463: таблица скорости на 3 монетах x 3 ТФ: эталон tk461_sim.trade / trade_fast (первые N событий, чистый счёт 28 ячеек D2a) против ядра tk463-sim
# (те же N событий, 1 поток; все события, 15 потоков, без записи gz). Итог /data/tk0463/bench2/BENCH2.DONE; таблица bench2.log: «монета ТФ | ref_s fast_s строк | rs_1поток_s | события, строк, rs_15_стена_s».
set -u
N=${1:-100}; B=/data/tk0463; OUT=$B/bench2; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um; W=/data/tk0461; FUND=/data/tk0455/fund; BIN=$B/bin/tk463-sim
mkdir -p $OUT; rm -f $OUT/BENCH2.DONE $OUT/bench2.log $OUT/*.tmp
export PYTHONPATH=/data/tk0455:$W:$B
for c in TWTUSDT PYTHUSDT ONGUSDT; do for tf in 60 240 1440; do echo "$c $tf"; done; done > $OUT/plan.txt
job() { c=$1; tf=$2
  $PY -c "import pandas as pd;pd.read_csv('$W/ev/$c-$tf.csv.gz').head($N).to_csv('$OUT/$c-$tf.head.csv',index=False)"
  py=$($PY $B/tk463_bench2.py $SRC/$c.csv.gz $tf $W/ev/$c-$tf.csv.gz $FUND $N 2>/dev/null)
  rs=$($BIN $SRC/$c.csv.gz $tf $OUT/$c-$tf.head.csv $FUND $B/out/cells.csv $OUT/$c-$tf.head.out.tmp --threads 1 2>&1 | grep -o "счёт [0-9.]* с" | head -1)
  echo "$c $tf | $py | head1 $rs" >$OUT/$c-$tf.line; }
export -f job; export OUT B BIN SRC W FUND PY N
xargs -P 9 -L1 bash -c 'job $0 $1' < $OUT/plan.txt
for c in TWTUSDT PYTHUSDT ONGUSDT; do for tf in 60 240 1440; do
  ne=$(( $(zcat $W/ev/$c-$tf.csv.gz | wc -l) - 1 ))
  r=$( { /usr/bin/time -f "стена %e" $BIN $SRC/$c.csv.gz $tf $W/ev/$c-$tf.csv.gz $FUND $B/out/cells.csv $OUT/full.out.tmp --threads 15 2>&1 | grep -E "стена|счёт|строк" | tr '\n' ' '; } 2>&1)
  echo "$c $tf | full15 events $ne | $r" >>$OUT/bench2.log; done; done
cat $OUT/*.line >>$OUT/bench2.log; rm -f $OUT/*.tmp
echo done > $OUT/BENCH2.DONE
