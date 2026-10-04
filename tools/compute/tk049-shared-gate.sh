#!/bin/bash
# tk049-shared-gate.sh <БИН> [СИМВОЛ]: пара «старый путь / ALPHA_SHARED_ENGINE=1» одним бинарником, одновременно, на сутках 01-15.
# Режим, который общий путь поддерживает: --busy-skip on --exit-group off --round-memo off. Итог: /data/tk049/<SYM>-shared-<БИН>/{metrics.txt,diff.txt,gate.txt}.
BIN=${1:?бинарник}; SYM=${2:-ATOMUSDT}; MON=jan; d=2026-01-15
H=/data/tk046/$MON/home; S=/data/tk048/$d-alpha-tk049-a; R=/data/tk049/$SYM-shared-$BIN
SRC=/data/tk049/ATOMUSDT-clean-alpha-tk049-h40/cmd.sh
mkdir -p $R; rm -rf $S/b5/.cellstmp-$SYM-old $S/b5/.cellstmp-$SYM-new
ln -sfn /opt/alpha-compute/bin/$BIN $S/bin/alpha-tk044k1-new
export HOME=$H; cd $S || exit 2
for m in old new; do
  sed "s#--busy-skip off#--busy-skip on --round-memo off#; s#--exit-group on#--exit-group off#; s#b5/.cellstmp-$SYM#b5/.cellstmp-$SYM-$m#; s#/data/tk049/ATOMUSDT-clean-alpha-tk049-h40/grid.log#$R/grid-$m.log#" $SRC > $R/cmd-$m.sh
done
( s=$(date +%s.%N); bash $R/cmd-old.sh; e=$(date +%s.%N); echo "old_wall_s $(echo "$e - $s" | bc)" >> $R/metrics.txt ) &
( s=$(date +%s.%N); ALPHA_SHARED_ENGINE=1 bash $R/cmd-new.sh; e=$(date +%s.%N); echo "new_wall_s $(echo "$e - $s" | bc)" >> $R/metrics.txt ) &
wait
diff -r $S/b5/.cellstmp-$SYM-old $S/b5/.cellstmp-$SYM-new > $R/diff.txt 2>&1
echo "diff_rc $? files $(ls $S/b5/.cellstmp-$SYM-old | wc -l)" > $R/gate.txt; touch $R/.gate
