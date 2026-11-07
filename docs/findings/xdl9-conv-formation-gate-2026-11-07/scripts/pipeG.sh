#!/bin/bash
# Калибровка C2 с Gate (r2 + r3) на формировании: K0 -> панели оборота -> ряды -> K2 -> меры -> сетки -> выбор -> проверки -> итог -> описательные таблицы
. "$(dirname "$0")/pipe_common.sh"
OUT=$C2/out
GF=--with-gate
mkdir -p $OUT
run k0 $PY conv_run.py k0 --gate-ready $HOME/xdl9/gate_ready
run turn $PY conv_run.py turn --out $OUT --mode calib --last-day 2478 --turn-dir $TD $GF
run turn_verify $PY conv_run.py turn-verify --out $OUT --mode calib --last-day 2478 --turn-dir $TD $GF
run turn_verify_gate $PY conv_run.py turn-verify --out $OUT --mode calib --last-day 2478 --turn-dir $TD $GF --verify-ex gate --verify-n 40
run series_calib_L1440 $PY conv_run.py series --out $OUT --mode calib --last-day 2478 --L 1440 --threads $TH --turn-dir $TD $GF
run series_calib_L4320 $PY conv_run.py series --out $OUT --mode calib --last-day 2478 --L 4320 --threads $TH --turn-dir $TD $GF
run series_train_L1440 $PY conv_run.py series --out $OUT --mode train --last-day 2471 --L 1440 --threads $TH --turn-dir $TD $GF
run series_train_L4320 $PY conv_run.py series --out $OUT --mode train --last-day 2471 --L 4320 --threads $TH --turn-dir $TD $GF
run k2_calib $PY conv_k2.py --out $OUT --mode calib --last-day 2478 --threads $TH --json $OUT/k2_calib.json $GF
run k2_train $PY conv_k2.py --out $OUT --mode train --last-day 2471 --threads $TH --json $OUT/k2_train.json $GF
LF=$($PY -c "import json;print(json.load(open('$OUT/k2_calib.json'))['L'])")
LT=$($PY -c "import json;print(json.load(open('$OUT/k2_train.json'))['L'])")
echo "G L_full=$LF L_train=$LT $(date -u +%FT%TZ)" >> $LOG/pipe.status
C=$OUT/calib
run halves $PY conv_k7.py halves --out $OUT $GF
run measure_train $PY conv_run.py measure --out $OUT --mode train --last-day 2471 --L $LT --tag train --threads $TH --turn-dir $TD $GF
run measure_full $PY conv_run.py measure --out $OUT --mode calib --last-day 2478 --L $LF --tag full --threads $TH --turn-dir $TD $GF
run measure_H0 $PY conv_run.py measure --out $OUT --mode calib --last-day 2478 --L $LF --tag H0 --pairs-file $OUT/pairs_H0.txt --threads $TH --turn-dir $TD $GF
run measure_H1 $PY conv_run.py measure --out $OUT --mode calib --last-day 2478 --L $LF --tag H1 --pairs-file $OUT/pairs_H1.txt --threads $TH --turn-dir $TD $GF
run grid_train $PY conv_run.py grid --out $OUT --mode train --last-day 2471 --L $LT --tag train --classpar $OUT/train/classpar_train.json --q5 $OUT/train/q5_train.json --threads $TH --turn-dir $TD $GF
run grid_full $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag full --classpar $C/classpar_full.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
run grid_H0 $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag H0 --pairs-file $OUT/pairs_H0.txt --classpar $C/classpar_H0.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
run grid_H1 $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag H1 --pairs-file $OUT/pairs_H1.txt --classpar $C/classpar_H1.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
run pick $PY conv_k7.py pick --out $OUT $GF
for cls in crypto eq; do
  [ -f $C/combo_train_$cls.json ] && run tv_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LT --tag tv_$cls --classes $cls --combos $C/combo_train_$cls.json --classpar $OUT/train/classpar_train.json --q5 $OUT/train/q5_train.json --threads $TH --turn-dir $TD $GF
  [ -f $C/combo_H0_$cls.json ] && run h1on0_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag h1on0_$cls --classes $cls --pairs-file $OUT/pairs_H1.txt --combos $C/combo_H0_$cls.json --classpar $C/classpar_H0.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
  [ -f $C/combo_H1_$cls.json ] && run h0on1_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag h0on1_$cls --classes $cls --pairs-file $OUT/pairs_H0.txt --combos $C/combo_H1_$cls.json --classpar $C/classpar_H1.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
  [ -f $C/combo_full_$cls.json ] && run k8grid_$cls $PY conv_run.py grid --out $OUT --mode calib --last-day 2478 --L $LF --tag k8_$cls --classes $cls --combos $C/combo_full_$cls.json --classpar $C/classpar_full.json --q5 $C/q5_full.json --trades-out --threads $TH --turn-dir $TD $GF
done
run final $PY conv_k7.py final --out $OUT --perm 1000 --L-full $LF $GF
run universe $PY conv_k7.py universe --out $OUT --last-day 2478 --L-full $LF $GF
run hslice $PY conv_hslice.py --out $OUT --classes crypto,eq $GF
for cls in crypto eq; do
  [ -d $C/grid_k8_$cls ] && run k8shift_$cls $PY conv_k8.py --out $OUT --cls $cls --turn-dir $TD --threads $TH $GF
  for fl in 0 10000; do
    [ -f $C/combo_full_$cls.json ] && run sens_f${fl}_$cls $PY conv_sens.py --floor $fl -- grid --out $OUT --mode calib --last-day 2478 --L $LF --tag sens_f${fl}_$cls --classes $cls --combos $C/combo_full_$cls.json --classpar $C/classpar_full.json --q5 $C/q5_full.json --threads $TH --turn-dir $TD $GF
  done
done
echo "DONE_G $(date -u +%FT%TZ)" >> $LOG/pipe.status
