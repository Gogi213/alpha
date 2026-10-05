#!/bin/bash
# TK-037 В-172: новые 112 суток (--steps-from-day) → ссылки в vroots, затем verify + validate по затронутым монето-месяцам (бинарник читает v4). Журнал done-v4check.log, маркер ALL_DONE_V4CHECK.
BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk037-validate}; L=/data/tk037; R=$L/vroots; LOGD=$L/verify-logs; OUT=$L/validate-out; P=${P:-6}
grep "не кратны" $L/missing-final.csv | while IFS=, read s d r; do b=e-${d:0:7}; mkdir -p $R/$b; ln -sfn $L/roots/$b/$s-$d.binlog $R/$b/$s-$d.binlog; echo "$b $s"; done | sort -u > $LOGD/list-v4check.txt
export BIN R LOGD OUT
cat $LOGD/list-v4check.txt | xargs -P $P -L1 bash -c 'b=$0; s=$1; t0=$(date +%s)
  $BIN lob verify --symbol $s --root $R/$b > $LOGD/$b-$s.log 2>&1; rc1=$?
  ls $R/$b/$s-20*.binlog > $OUT/$b-$s.list
  $BIN lob validate --list $OUT/$b-$s.list --out-dir $OUT/$b-$s --threads 1 > $LOGD/$b-$s.validate.log 2>&1; rc2=$?
  echo "$b $s rc=$rc1 validate_rc=$rc2 $(( $(date +%s)-t0 ))s" >> $LOGD/done-v4check.log'
touch $LOGD/ALL_DONE_V4CHECK
