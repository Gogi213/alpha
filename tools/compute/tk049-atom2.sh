#!/bin/bash
# tk049-atom2.sh [СИМВОЛ]: чистый профиль кругов одного символа суток 01-15 (без --extra-runs), perf без стеков; итог /data/tk049/<SYM>-clean/.
SYM=${1:-ATOMUSDT}; BIN=${2:-alpha-tk049-a}; MON=jan; d=2026-01-15
H=/data/tk046/$MON/home; S=/data/tk048/$d-alpha-tk049-a; R=/data/tk049/$SYM-clean-$BIN
mkdir -p $R; rm -rf $S/b5/.cellstmp-$SYM
ln -sfn /opt/alpha-compute/bin/$BIN $S/bin/alpha-tk044k1-new; mkdir -p $R; export HOME=$H ALPHA_ATTEMPT_STATS=1; cd $S || exit 2
sed -n 3p $H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh | sed "s# lob bounce-grid # lob bounce-grid --symbol $SYM #; s#b5/.cellstmp-$d#b5/.cellstmp-$SYM#; s#b5/.cellstmp-$d.log#$R/grid.log#; s#--extra-runs [^ ]*##" > $R/cmd.sh
s=$(date +%s.%N)
perf record -F 999 -g -o $R/perf.data -- bash $R/cmd.sh > $R/run.out 2> $R/run.err
e=$(date +%s.%N); echo "wall_s $(echo "$e - $s" | bc)" > $R/metrics.txt
perf report -i $R/perf.data --no-children --sort symbol --stdio -g none 2>/dev/null | grep -v "^#" | grep -v "^$" | head -50 > $R/perf-self.txt
touch $R/.done
