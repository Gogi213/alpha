#!/bin/bash
# tk049-gate.sh <БИН> [СИМВОЛ]: чистый прогон кругов (tk049-atom2.sh) + diff клеточных файлов против базы /data/tk049/<SYM>-base/cells; итог diff.txt и .gate в каталоге прогона.
BIN=${1:?бинарник}; SYM=${2:-ATOMUSDT}; R=/data/tk049/$SYM-clean-$BIN
bash /data/tk049/tk049-atom2.sh $SYM $BIN
diff -r /data/tk049/$SYM-base/cells /data/tk048/2026-01-15-alpha-tk049-a/b5/.cellstmp-$SYM > $R/diff.txt 2>&1
echo "diff_rc $?" > $R/gate.txt; touch $R/.gate
