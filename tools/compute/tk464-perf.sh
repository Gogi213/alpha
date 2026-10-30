#!/bin/bash
# TK-464 (2): профиль пары ТФ 1440 (TWT, 449 ячеек, --pair) через perf record, 1 поток. Итог /data/tk0464/perf/perf.DONE
# alsched submit --cls prod --kind gate --cores 1 --name tk464-perf --max-runtime 20m -- bash /data/tk0464/perf/tk464-perf.sh
set -u
c=${1:-TWTUSDT}; tf=${2:-1440}; O=/data/tk0464/perf; BIN=${BIN:-/data/tk0464/bin/tk464-pair-sim}
SRC=/srv/alpha-sda/candles-1m/binance-um; FUND=/data/tk0455/fund; W=/data/tk0461; B=/data/tk0463; CELLS=/data/tk0464/bench/cells449.csv
mkdir -p $O; rm -f $O/perf.DONE; cd $O
zcat $W/ev/$c-$tf.csv.gz >$O/ev.csv
file $BIN >$O/info.txt 2>&1
perf record -F 499 -g -o $O/perf.data -- $BIN $SRC/$c.csv.gz $tf $O/ev.csv $FUND $CELLS $O/out.csv --threads 1 --pair $B/out/$c-$tf.ctrl-in.csv >$O/run.log 2>&1
echo "rc $?" >>$O/run.log
perf report -i $O/perf.data --no-children --sort symbol --stdio -g none 2>/dev/null | head -60 >$O/flat.txt
perf report -i $O/perf.data --children --sort symbol --stdio -g none 2>/dev/null | head -40 >$O/children.txt
rm -f $O/ev.csv $O/out.csv
echo done >$O/perf.DONE
