#!/bin/bash
# Послесчётные шаги: сверка --no-gate с прежним прогоном, выжимки, добавки, таблицы, подсказки Gate, addendum-списки
. "$(dirname "$0")/pipe_common.sh"
X=$C2/extra
S=$C2/summary
OLD=$HOME/xdl9/c2
mkdir -p $S
export PYTHONDONTWRITEBYTECODE=1
# 1. K1b и G-C2-8: код не менялся (sha256 conv_k1b.py, conv_size.py равны addendum 07.11), данные от Gate не зависят -> копия прежних выходов (раскрыть в отчёте)
for D in out out_nogate; do
  mkdir -p $C2/$D/size $C2/$D/k7
  cp $OLD/out/size/g_c2_8.json $C2/$D/size/g_c2_8.json
  cp $OLD/out/k7/k1b.json $C2/$D/k7/k1b.json
done
# 2. сверка --no-gate (новый код) с прежним прогоном c2/out (старый код до G1): массивы сеток, выбор, ячейки
{
echo "# сверка out_nogate (новый код, --no-gate) с прежним ~/xdl9/c2/out (код f47dff41)"
for pair in "out_nogate out" "out_nogate_noq5 out_noq5"; do
  set -- $pair
  N=$C2/$1; O=$OLD/$2
  echo "## $1 против c2/$2"
  for g in grid_full grid_train grid_H0 grid_H1 grid_k8_crypto; do
    sub=calib; [ "$g" = grid_train ] && sub=calib
    gd=$N/$sub/$g; od=$O/$sub/$g
    [ "$g" = grid_train ] && { gd=$N/train/grid_train; od=$O/train/grid_train; }
    for f in $(ls $gd/crypto 2>/dev/null) ; do
      if cmp -s $gd/crypto/$f $od/crypto/$f; then r=eq; else r=DIFF; fi
      echo "$g crypto/$f $r"
    done
    for f in $(ls $gd/eq 2>/dev/null) ; do
      if cmp -s $gd/eq/$f $od/eq/$f; then r=eq; else r=DIFF; fi
      echo "$g eq/$f $r"
    done
  done
  for f in k7/selection_crypto.json k7/selection_eq.json k7/cells_crypto.csv k7/cells_eq.csv k7/grid_class_crypto.csv k7/grid_class_eq.csv k7/grid_cells_crypto.csv k7/grid_cells_eq.csv k7/summary.json calib/classpar_full.json calib/q5_full.json calib/classpar_H0.json calib/classpar_H1.json train/classpar_train.json; do
    [ -f $N/$f ] || { echo "$f отсутствует в новом"; continue; }
    if cmp -s $N/$f $O/$f; then echo "$f eq"; else echo "$f DIFF"; fi
  done
done
} > $S/nogate_vs_old.txt 2>&1
# 3. выжимки и добавки
for pair in "out gate_q5" "out_noq5 gate_noq5" "out_nogate nogate_q5" "out_nogate_noq5 nogate_noq5"; do
  set -- $pair
  nice -n 10 $PY $X/gx_extract.py --out $C2/$1 --name $2 --dest $S > $LOG/ex_$2.log 2>&1 || echo "FAIL extract $2" >> $LOG/pipe.status
  nice -n 10 $PY $X/gx_extra.py --out $C2/$1 --name $2 --dest $S >> $LOG/ex_$2.log 2>&1 || echo "FAIL extra $2" >> $LOG/pipe.status
done
# 4. подсказки: все связки Gate (c1d2 = 1) на формировании
nice -n 10 $PY $X/gx_hints_extra.py --src $C2/out --hx $C2/out_hx --members $C2/hints_membership_gate.csv > $LOG/hints_extra.log 2>&1 || echo "FAIL hints_extra" >> $LOG/pipe.status
# 5. таблицы отчёта (conv_report): Gate и без Gate (раскладка ng/ для N5 = out_noq5 рядом)
mkdir -p $C2/ng; ln -sfn $C2/out_nogate $C2/ng/out; ln -sfn $C2/out_nogate_noq5 $C2/ng/out_noq5
nice -n 10 $PY $X/gx_tables.py $C2/out $C2/report_gate > $LOG/tables_gate.log 2>&1 || echo "FAIL tables_gate" >> $LOG/pipe.status
nice -n 10 $PY $X/gx_tables.py $C2/ng/out $C2/report_nogate > $LOG/tables_nogate.log 2>&1 || echo "FAIL tables_nogate" >> $LOG/pipe.status
# 6. addendum-списки (conv_addendum, набор Gate)
nice -n 10 $PY $X/gx_addendum.py $C2/addendum_run docs/research/P-38-calib-gate-2026-11-07 P-38-params-calib-gate-2026-11-07.csv "07.11 G0 ч.2 (Gate)" > $LOG/addendum_run.log 2>&1 || echo "FAIL addendum" >> $LOG/pipe.status
echo "POST_DONE $(date -u +%FT%TZ)" >> $LOG/pipe.status
