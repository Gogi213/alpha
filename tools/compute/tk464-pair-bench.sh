#!/bin/bash
# TK-464: замер парного режима --pair ядра на 449 ячейках (прокси, cells449.csv из tk464-d2b-bench.sh): монета x 3 ТФ, ВСЕ события
# /data/tk0461/ev/<монета>-<ТФ>.csv.gz + контроли out/<монета>-<ТФ>.ctrl-in.csv. Итог: стена/ЦП/пик памяти, строк групп.
# bash tk464-pair-bench.sh [монета] [потоки] ; лог /data/tk0464/pairbench/bench.log, итог bench.DONE
set -u
c=${1:-TWTUSDT}; th=${2:-15}; B=/data/tk0463; O=/data/tk0464/pairbench; BIN=${BIN:-/data/tk0464/bin/tk464-pair-sim}
SRC=/srv/alpha-sda/candles-1m/binance-um; FUND=/data/tk0455/fund; W=/data/tk0461; CELLS=/data/tk0464/bench/cells449.csv
mkdir -p $O; rm -f $O/bench.DONE $O/bench.log
echo "ячеек $(( $(wc -l <$CELLS) - 1 )), бинарник $BIN, потоков $th" >>$O/bench.log
for tf in 60 240 1440; do
  ev=$O/$c-$tf.ev.csv; zcat $W/ev/$c-$tf.csv.gz >$ev
  ct=$B/out/$c-$tf.ctrl-in.csv
  echo "== $c ТФ$tf событий $(( $(wc -l <$ev) - 1 )) контролей $(( $(wc -l <$ct) - 1 ))" >>$O/bench.log
  /usr/bin/time -f "стена %e с, ЦП %U+%S с, пик %M КБ" $BIN $SRC/$c.csv.gz $tf $ev $FUND $CELLS $O/$c-$tf.pair.csv --threads $th --pair $ct >>$O/bench.log 2>&1
  ls -l $O/$c-$tf.pair.csv | awk '{print "выход, байт: " $5}' >>$O/bench.log
  rm -f $ev
done
echo done >$O/bench.DONE
