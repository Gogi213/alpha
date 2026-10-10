#!/bin/bash
# tk049-stats.sh <БИН> [СИМВОЛ]: счётчики общей ленты (ALPHA_SHARED_STATS) на ATOM 01-15: пачка 66 и пачка 8 одновременно; строки SHARED_STATS — в stats-*.txt.
BIN=${1:?бинарник}; SYM=${2:-ATOMUSDT}; d=2026-01-15
H=/data/tk046/jan/home; S=/data/tk048/$d-alpha-tk049-a; R=/data/tk049/$SYM-shared-alpha-tk049-s1
ln -sfn /opt/alpha-compute/bin/$BIN $S/bin/alpha-tk044k1-new
export HOME=$H; cd $S || exit 2
for n in ${PACKS:-66 8}; do
  sed "s#b5/.cellstmp-$SYM-new#b5/.cellstmp-$SYM-st$n#; s#grid-new.log#grid-st$n.log#" $R/cmd-new.sh > $R/cmd-st$n.sh
  rm -rf b5/.cellstmp-$SYM-st$n
  ( s=$(date +%s.%N); ALPHA_SHARED_STATS=1 ALPHA_SHARED_CELLS=$n ALPHA_SHARED_ENGINE=1 bash $R/cmd-st$n.sh > $R/out-st$n.txt 2>&1; e=$(date +%s.%N); echo "wall_s $(echo "$e - $s" | bc)" > $R/stats-$n.txt; grep -h SHARED_STATS $R/grid-st$n.log >> $R/stats-$n.txt ) &
done
wait
touch $R/.stats
