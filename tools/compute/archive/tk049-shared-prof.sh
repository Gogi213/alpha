#!/bin/bash
# tk049-shared-prof.sh <БИН> [СИМВОЛ]: общий путь ATOM 01-15 — perf (пачка 8) одновременно с пачкой 66 (замер стены); команды из tk049-shared-gate.sh.
BIN=${1:?бинарник}; SYM=${2:-ATOMUSDT}; d=2026-01-15
H=/data/tk046/jan/home; S=/data/tk048/$d-alpha-tk049-a; R=/data/tk049/$SYM-shared-$BIN
ln -sfn /opt/alpha-compute/bin/$BIN $S/bin/alpha-tk044k1-new
export HOME=$H; cd $S || exit 2
sed "s#b5/.cellstmp-$SYM-new#b5/.cellstmp-$SYM-p#; s#grid-new.log#grid-p.log#" $R/cmd-new.sh > $R/cmd-p.sh
sed "s#b5/.cellstmp-$SYM-new#b5/.cellstmp-$SYM-w#; s#grid-new.log#grid-w.log#" $R/cmd-new.sh > $R/cmd-w.sh
rm -rf b5/.cellstmp-$SYM-p b5/.cellstmp-$SYM-w
( ALPHA_SHARED_ENGINE=1 perf record -F 499 -o $R/perf.data -- bash $R/cmd-p.sh; perf report -i $R/perf.data --no-children --sort symbol --stdio 2>/dev/null | grep -v "^#" | grep -v "^$" | head -40 > $R/perf-self.txt ) &
( s=$(date +%s.%N); ALPHA_SHARED_CELLS=66 ALPHA_SHARED_ENGINE=1 bash $R/cmd-w.sh; e=$(date +%s.%N); echo "cells66_wall_s $(echo "$e - $s" | bc)" > $R/metrics66.txt ) &
wait
diff -r b5/.cellstmp-$SYM-old b5/.cellstmp-$SYM-w > $R/diff66.txt 2>&1; echo "diff66_rc $?" >> $R/metrics66.txt; touch $R/.prof
