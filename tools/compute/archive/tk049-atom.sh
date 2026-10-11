#!/bin/bash
# tk049-atom.sh [СИМВОЛ]: perf -g по одному символу суток 01-15 в песочнице tk048 (alpha-tk049-a); итог /data/tk049/<SYM>/.
SYM=${1:-ATOMUSDT}; MON=jan; d=2026-01-15
H=/data/tk046/$MON/home; S=/data/tk048/$d-alpha-tk049-a; R=/data/tk049/$SYM
mkdir -p $R; rm -rf $S/b5/.cellstmp-$SYM
export HOME=$H ALPHA_ATTEMPT_STATS=1; cd $S || exit 2
sed -n 3p $H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh | sed "s# lob bounce-grid # lob bounce-grid --symbol $SYM #; s#b5/.cellstmp-$d#b5/.cellstmp-$SYM#; s#b5/.cellstmp-$d.log#$R/grid.log#" > $R/cmd.sh
s=$(date +%s.%N)
perf record -F 199 --call-graph dwarf,8192 -o $R/perf.data -- bash $R/cmd.sh > $R/run.out 2> $R/run.err
e=$(date +%s.%N); echo "wall_s $(echo "$e - $s" | bc)" > $R/metrics.txt
perf report -i $R/perf.data --no-children --sort symbol --stdio -g none 2>/dev/null | grep -v "^#" | grep -v "^$" | head -40 > $R/perf-self.txt
perf report -i $R/perf.data --children --sort symbol --stdio -g none 2>/dev/null | grep -v "^#" | grep -v "^$" | head -60 > $R/perf-children.txt
touch $R/.done
