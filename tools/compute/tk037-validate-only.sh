#!/bin/bash
# TK-037: добор lob validate для монето-месяцев, у которых в done.log нет validate_rc (проверены verify2). Читателей по PD на диск.
W=${W:-/opt/alpha-compute/bin/alpha-tk037-validate}; R=${R:-/data/tk037/vroots}; LOGD=${LOGD:-/data/tk037/verify-logs}
OUT=${OUT:-/data/tk037/validate-out}; PD=${PD:-3}
unit() { local b=$1 s=$2 t0=$(date +%s) rc
  ls $R/$b/$s-20*.binlog > $OUT/$b-$s.list 2>/dev/null
  $W lob validate --list $OUT/$b-$s.list --out-dir $OUT/$b-$s --threads 1 > $LOGD/$b-$s.validate.log 2>&1; rc=$?
  echo "$b $s validate_rc=$rc $(( $(date +%s)-t0 ))s" >> $LOGD/done-validate-only.log; }
export -f unit; export W R LOGD OUT
grep -v validate_rc $LOGD/done.log | awk '{print $1, $2}' | while read b s; do
  f=$(ls $R/$b/$s-20*.binlog | head -1); case "$(readlink -f $f)" in /data/*) d=sdb;; *) d=sda;; esac; echo "$d $b $s"; done > $LOGD/list-vonly.txt
for d in sda sdb; do grep "^$d " $LOGD/list-vonly.txt | cut -d' ' -f2- | xargs -P $PD -L1 bash -c 'unit $0 $1' & done
wait; touch $LOGD/ALL_DONE_VONLY
