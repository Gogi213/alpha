#!/bin/bash
# Контроль "без Gate" (--no-gate): те же этапы на универсуме 8 бирж; ряды (кэш) общие с набором Gate (ячейки 8 бирж не зависят от Gate), K2 и всё остальное - заново
. "$(dirname "$0")/pipe_common.sh"
SRC=$C2/out; OUT=$C2/out_nogate; GF=--no-gate
mkdir -p $OUT/calib $OUT/train
[ -e $OUT/calib/cache ] || ln -s $SRC/calib/cache $OUT/calib/cache
[ -e $OUT/train/cache ] || ln -s $SRC/train/cache $OUT/train/cache
run n_k2_calib $PY conv_k2.py --out $OUT --mode calib --last-day 2478 --threads $TH --json $OUT/k2_calib.json $GF
run n_k2_train $PY conv_k2.py --out $OUT --mode train --last-day 2471 --threads $TH --json $OUT/k2_train.json $GF
LF=$($PY -c "import json;print(json.load(open('$OUT/k2_calib.json'))['L'])")
LT=$($PY -c "import json;print(json.load(open('$OUT/k2_train.json'))['L'])")
echo "N L_full=$LF L_train=$LT $(date -u +%FT%TZ)" >> $LOG/pipe.status
C=$OUT/calib
run n_halves $PY conv_k7.py halves --out $OUT $GF
run n_measure_train $PY conv_run.py measure --out $OUT --mode train --last-day 2471 --L $LT --tag train --threads $TH --turn-dir $TD $GF
run n_measure_full $PY conv_run.py measure --out $OUT --mode calib --last-day 2478 --L $LF --tag full --threads $TH --turn-dir $TD $GF
run n_measure_H0 $PY conv_run.py measure --out $OUT --mode calib --last-day 2478 --L $LF --tag H0 --pairs-file $OUT/pairs_H0.txt --threads $TH --turn-dir $TD $GF
run n_measure_H1 $PY conv_run.py measure --out $OUT --mode calib --last-day 2478 --L $LF --tag H1 --pairs-file $OUT/pairs_H1.txt --threads $TH --turn-dir $TD $GF
run n_grid_train $PY conv_run.py grid --out $OUT --mode train --last-day 2471 --L $LT --tag train --classpar $OUT/train/classpar_train.json --q5 $OUT/train/q5_train.json --threads $TH --turn-dir $TD $GF
run n_grid_full $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag full --classpar $C/classpar_full.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
run n_grid_H0 $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag H0 --pairs-file $OUT/pairs_H0.txt --classpar $C/classpar_H0.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
run n_grid_H1 $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag H1 --pairs-file $OUT/pairs_H1.txt --classpar $C/classpar_H1.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
run n_pick $PY conv_k7.py pick --out $OUT $GF
for cls in crypto eq; do
  [ -f $C/combo_train_$cls.json ] && run n_tv_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LT --tag tv_$cls --classes $cls --combos $C/combo_train_$cls.json --classpar $OUT/train/classpar_train.json --q5 $OUT/train/q5_train.json --threads $TH --turn-dir $TD $GF
  [ -f $C/combo_H0_$cls.json ] && run n_h1on0_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag h1on0_$cls --classes $cls --pairs-file $OUT/pairs_H1.txt --combos $C/combo_H0_$cls.json --classpar $C/classpar_H0.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
  [ -f $C/combo_H1_$cls.json ] && run n_h0on1_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag h0on1_$cls --classes $cls --pairs-file $OUT/pairs_H0.txt --combos $C/combo_H1_$cls.json --classpar $C/classpar_H1.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
  [ -f $C/combo_full_$cls.json ] && run n_k8grid_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag k8_$cls --classes $cls --combos $C/combo_full_$cls.json --classpar $C/classpar_full.json --q5 $C/q5_full.json --trades-out --threads $TH --turn-dir $TD $GF
done
run n_final $PY conv_k7.py final --out $OUT --perm 1000 --L-full $LF $GF
run n_universe $PY conv_k7.py universe --out $OUT --last-day 2478 --L-full $LF $GF
run n_hslice $PY conv_hslice.py --out $OUT --classes crypto,eq $GF
for cls in crypto eq; do
  [ -d $C/grid_k8_$cls ] && run n_k8shift_$cls $PY conv_k8.py --out $OUT --cls $cls --turn-dir $TD --threads $TH $GF
done
echo "DONE_N $(date -u +%FT%TZ)" >> $LOG/pipe.status
