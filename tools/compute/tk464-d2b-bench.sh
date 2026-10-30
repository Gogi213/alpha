#!/bin/bash
# TK-464: замер D2b-прохода ядра в режиме --agg 1 на 449 ячейках (прокси): TWT x 3 ТФ, события = входы контроля гейта
# (out/<монета>-<ТФ>.ctrl-in.csv, события+контроли), ячейки = 7 D2b-ячеек гейта без SS/OT1 (у файла нет ss_bps/ot1_bps) + 28 ячеек D2a, по кругу до 449.
# bash tk464-d2b-bench.sh [монета] [потоки] ; лог /data/tk0464/bench/bench.log, итог bench.DONE
set -u
c=${1:-TWTUSDT}; th=${2:-15}; B=/data/tk0463; O=/data/tk0464/bench; BIN=${BIN:-/data/tk0464/bin/tk464-sim}
SRC=/srv/alpha-sda/candles-1m/binance-um; mkdir -p $O; rm -f $O/bench.DONE $O/bench.log
{ echo stop,m,take,par,be_r,ot,otp
  grep -E '^SF|^ST' $B/d2b/cells.csv | grep -v 'OT1'
  tail -n +2 $B/out/cells.csv | sed 's/$/,,,/'; } >$O/base.csv
n=$(( $(wc -l <$O/base.csv) - 1 )); { head -1 $O/base.csv; for i in $(seq 0 448); do sed -n "$(( i % n + 2 ))p" $O/base.csv; done; } >$O/cells449.csv
echo "ячеек $(( $(wc -l <$O/cells449.csv) - 1 )) (базовых $n)" >>$O/bench.log
for tf in 60 240 1440; do
  inp=$B/out/$c-$tf.ctrl-in.csv
  echo "== $c ТФ$tf событий $(( $(wc -l <$inp) - 1 )) потоков $th" >>$O/bench.log
  /usr/bin/time -f "стена %e с, ЦП %U+%S с, пик %M КБ" $BIN $SRC/$c.csv.gz $tf $inp /data/tk0455/fund $O/cells449.csv $O/$c-$tf.agg.csv --threads $th --agg 1 >>$O/bench.log 2>&1
  ls -l $O/$c-$tf.agg.csv | awk '{print "агрегат, байт: " $5}' >>$O/bench.log
done
echo done >$O/bench.DONE
