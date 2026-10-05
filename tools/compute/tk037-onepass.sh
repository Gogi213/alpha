#!/bin/bash
# TK-037/TK-038: один проход по диску на монето-месяц — lob verify (маркер verify-<SYM>.status) и сразу lob validate
# (внутри часа) на тех же файлах из страничного кэша. Читателей по PD на диск (HDD: десятки случайных потоков
# давали ~20 МБ/с суммарно, io-pressure 80 %). Уже сделанные (rc=0 в done.log) пропускаются.
V=${V:-/opt/alpha-compute/bin/alpha-tk035-steps}; W=${W:-/opt/alpha-compute/bin/alpha-tk037-validate}
R=${R:-/data/tk037/vroots}; LOGD=${LOGD:-/data/tk037/verify-logs}; OUT=${OUT:-/data/tk037/validate-out}; PD=${PD:-3}
mkdir -p "$LOGD" "$OUT"; touch "$LOGD/done.log"
unit() { # $1=месяц $2=символ
  local b=$1 s=$2 t0=$(date +%s) rc1 rc2
  $V lob verify --symbol $s --root $R/$b > $LOGD/$b-$s.log 2>&1; rc1=$?
  ls $R/$b/$s-20*.binlog > $OUT/$b-$s.list 2>/dev/null
  $W lob validate --list $OUT/$b-$s.list --out-dir $OUT/$b-$s --threads 1 > $LOGD/$b-$s.validate.log 2>&1; rc2=$?
  echo "$b $s rc=$rc1 validate_rc=$rc2 $(( $(date +%s)-t0 ))s" >> $LOGD/done.log
}
export -f unit; export V W R LOGD OUT
for m in $R/e-*; do b=$(basename $m); ls $m | grep -a '\.binlog$' | sed 's/-20[0-9-]*\(-p[0-9]*\)\?\.binlog$//' | sort -u | while read s; do
  grep -q "^$b $s rc=0" $LOGD/done.log && continue
  f=$(ls $m/$s-20*.binlog | head -1); case "$(readlink -f $f)" in /data/*) d=sdb;; *) d=sda;; esac
  echo "$d $b $s"; done; done > $LOGD/list2.txt
wc -l < $LOGD/list2.txt > $LOGD/count2
for d in sda sdb; do
  grep "^$d " $LOGD/list2.txt | cut -d' ' -f2- | xargs -P $PD -L1 bash -c 'unit $0 $1' &
done
wait
touch $LOGD/ALL_DONE2
