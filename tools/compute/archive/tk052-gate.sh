#!/bin/bash
# tk052-gate.sh <БИН> [СИМВОЛ]: гейт «байт в байт» учёта ALPHA_E2E — один бинарник, без учёта и с учётом, сутки 01-15, подряд.
# Итог: /data/tk052/<SYM>-gate-<БИН>/{metrics.txt,diff.txt,gate.txt,e2e.jsonl}.
BIN=${1:?бинарник}; SYM=${2:-ATOMUSDT}; MON=jan; d=2026-01-15
H=/data/tk046/$MON/home; S=/data/tk048/$d-alpha-tk049-a; R=/data/tk052/$SYM-gate-$BIN
SRC=/data/tk049/ATOMUSDT-clean-alpha-tk049-h40/cmd.sh
mkdir -p $R; rm -rf $S/b5/.cellstmp-$SYM-off $S/b5/.cellstmp-$SYM-on $R/e2e.jsonl $R/metrics.txt
export HOME=$H; cd $S || exit 2
for m in off on; do
  sed "s#bin/alpha-tk044k1-new#/opt/alpha-compute/bin/$BIN#; s#b5/.cellstmp-ATOMUSDT#b5/.cellstmp-$SYM-$m#; s#--symbol ATOMUSDT#--symbol $SYM#; s#/data/tk049/ATOMUSDT-clean-alpha-tk049-h40/grid.log#$R/grid-$m.log#" $SRC > $R/cmd-$m.sh
done
s=$(date +%s.%N); bash $R/cmd-off.sh; e=$(date +%s.%N); echo "off_wall_s $(echo "$e - $s" | bc)" >> $R/metrics.txt
s=$(date +%s.%N); ALPHA_E2E=$R/e2e.jsonl bash $R/cmd-on.sh; e=$(date +%s.%N); echo "on_wall_s $(echo "$e - $s" | bc)" >> $R/metrics.txt
diff -r $S/b5/.cellstmp-$SYM-off $S/b5/.cellstmp-$SYM-on > $R/diff.txt 2>&1
echo "diff_rc $? files $(ls $S/b5/.cellstmp-$SYM-off | wc -l)" > $R/gate.txt; touch $R/.gate
