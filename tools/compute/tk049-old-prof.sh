#!/bin/bash
# tk049-old-prof.sh <БИН> [СИМВОЛ]: perf старого пути (окна) одновременно с perf общего пути (пачка 66) — одна нагрузка, self-символы рядом; команды из tk049-shared-gate.sh/prof.sh.
BIN=${1:?бинарник}; SYM=${2:-ATOMUSDT}; d=2026-01-15
H=/data/tk046/jan/home; S=/data/tk048/$d-alpha-tk049-a; R=/data/tk049/$SYM-shared-$BIN
export HOME=$H; cd $S || exit 2
sed "s#b5/.cellstmp-$SYM-old#b5/.cellstmp-$SYM-po#; s#grid-old.log#grid-po.log#" $R/cmd-old.sh > $R/cmd-po.sh
sed "s#b5/.cellstmp-$SYM-new#b5/.cellstmp-$SYM-p6#; s#grid-new.log#grid-p6.log#" $R/cmd-new.sh > $R/cmd-p6.sh
rm -rf b5/.cellstmp-$SYM-po b5/.cellstmp-$SYM-p6
rep() { perf report -i $1 --no-children --sort symbol --stdio 2>/dev/null | grep -v "^#" | grep -v "^$" | head -30 > $2; }
( s=$(date +%s.%N); perf record -F 499 -o $R/perf-po.data -- bash $R/cmd-po.sh; e=$(date +%s.%N); echo "old_wall_s $(echo "$e - $s" | bc)" > $R/metrics-po.txt; rep $R/perf-po.data $R/perf-po-self.txt ) &
( s=$(date +%s.%N); ALPHA_SHARED_CELLS=66 ALPHA_SHARED_ENGINE=1 perf record -F 499 -o $R/perf-p6.data -- bash $R/cmd-p6.sh; e=$(date +%s.%N); echo "p66_wall_s $(echo "$e - $s" | bc)" > $R/metrics-p6.txt; rep $R/perf-p6.data $R/perf-p6-self.txt ) &
wait
touch $R/.prof2
