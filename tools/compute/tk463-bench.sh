#!/bin/bash
# TK-463: замер ядра tk463-sim на одной монете: загрузка / счёт / запись gz (уровни) по файлу входов контроля из гейта.
# bash tk463-bench.sh <монета> <тф>   (после tk463-gate.sh: нужен /data/tk0463/out/<монета>-<тф>.ctrl-in.csv)
set -u
c=${1:-TWTUSDT}; tf=${2:-60}; B=/data/tk0463; OUT=$B/out; BIN=$B/bin/tk463-sim; SRC=/srv/alpha-sda/candles-1m/binance-um
run() { echo "== $*"; /usr/bin/time -f "стена %e с, ЦП %U+%S с, пик %M КБ" $BIN $SRC/$c.csv.gz $tf $OUT/$c-$tf.ctrl-in.csv /data/tk0455/fund $OUT/cells.csv "$@" 2>&1; }
run $B/bench-null.csv --threads 1
run $B/bench-g6.csv.gz --threads 1
run $B/bench-g1.csv.gz --threads 1 --gz 1
run $B/bench-null4.csv --threads 4
run $B/bench-null15.csv --threads 15
rm -f $B/bench-*.csv $B/bench-*.csv.gz
echo DONE
