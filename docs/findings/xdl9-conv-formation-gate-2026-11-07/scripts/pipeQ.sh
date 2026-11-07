#!/bin/bash
# Вариант "Q5 выключен" (V2) для набора: те же числа класса, сетки без списка Q5, то же правило выбора и допуска.
# usage: pipeQ.sh SRC_NAME DST_NAME GATEFLAG PREFIX
. "$(dirname "$0")/pipe_common.sh"
SRC=$C2/$1; OUT=$C2/$2; GF=$3; PFX=$4
LF=$($PY -c "import json;print(json.load(open('$SRC/k2_calib.json'))['L'])")
LT=$($PY -c "import json;print(json.load(open('$SRC/k2_train.json'))['L'])")
C=$OUT/calib
rm -rf $OUT; mkdir -p $OUT/calib $OUT/train
ln -s $(readlink -f $SRC/calib/cache) $OUT/calib/cache; ln -s $(readlink -f $SRC/train/cache) $OUT/train/cache
cp $SRC/calib/classpar_full.json $SRC/calib/classpar_H0.json $SRC/calib/classpar_H1.json $OUT/calib/; cp $SRC/train/classpar_train.json $OUT/train/
cp $SRC/pairs_H0.txt $SRC/pairs_H1.txt $OUT/
cp $SRC/k2_calib.json $SRC/k2_train.json $OUT/
run ${PFX}_grid_train $PY conv_run.py grid --out $OUT --mode train --last-day 2471 --L $LT --tag train --classpar $OUT/train/classpar_train.json --threads $TH --turn-dir $TD $GF
run ${PFX}_grid_full $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag full --classpar $C/classpar_full.json --threads $TH --turn-dir $TD $GF
run ${PFX}_grid_H0 $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag H0 --pairs-file $OUT/pairs_H0.txt --classpar $C/classpar_H0.json --threads $TH --turn-dir $TD $GF
run ${PFX}_grid_H1 $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag H1 --pairs-file $OUT/pairs_H1.txt --classpar $C/classpar_H1.json --threads $TH --turn-dir $TD $GF
run ${PFX}_pick $PY conv_k7.py pick --out $OUT $GF
for cls in crypto eq; do
  [ -f $C/combo_train_$cls.json ] && run ${PFX}_tv_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LT --tag tv_$cls --classes $cls --combos $C/combo_train_$cls.json --classpar $OUT/train/classpar_train.json --threads $TH --turn-dir $TD $GF
  [ -f $C/combo_H0_$cls.json ] && run ${PFX}_h1on0_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag h1on0_$cls --classes $cls --pairs-file $OUT/pairs_H1.txt --combos $C/combo_H0_$cls.json --classpar $C/classpar_H0.json --threads $TH --turn-dir $TD $GF
  [ -f $C/combo_H1_$cls.json ] && run ${PFX}_h0on1_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag h0on1_$cls --classes $cls --pairs-file $OUT/pairs_H0.txt --combos $C/combo_H1_$cls.json --classpar $C/classpar_H1.json --threads $TH --turn-dir $TD $GF
done
run ${PFX}_final $PY conv_k7.py final --out $OUT --perm 1000 --L-full $LF $GF
echo "DONE_Q $2 $(date -u +%FT%TZ)" >> $LOG/pipe.status
