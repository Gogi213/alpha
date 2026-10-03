#!/bin/bash
# TK-042: (A) докачка 97 суток 09.2026 (tk037-one.sh), (B) verify+validate сентября 11 монет по объединённому корню vroots/e-2026-09
# (+ симлинки суток e-sep), (C) validate внутри часа по старым эпохам для суток пула (not-run). Последовательно, HDD.
# env: CSV=/data/tk037/tk042/completeness.csv  W/V бинарники. Маркер конца: /data/tk037/tk042/ALL_DONE
D=/data/tk037/tk042; CSV=${CSV:-$D/completeness.csv}; mkdir -p $D
V=${V:-/opt/alpha-compute/bin/alpha-tk035-steps}; W=${W:-/opt/alpha-compute/bin/alpha-tk037-validate}
VR=/data/tk037/vroots/e-2026-09; OUT=/data/tk037/validate-out; LOGD=/data/tk037/verify-logs
SYMS="ACEUSDT AKEUSDT BRUSDT BTWUSDT CASHCATUSDT FFUSDT FLOCKUSDT UAIUSDT VVVUSDT XPLUSDT ZROUSDT"
echo "A $(date +%T)" >> $D/steps.log
awk -F, 'NR>1 && $6=="missing"{print $1,$2}' $CSV > $D/need97.txt
export LOG=$D/one.log; xargs -P 4 -L1 /root/tk037-one.sh < $D/need97.txt
echo "B $(date +%T)" >> $D/steps.log
mkdir -p $D/old
for s in $SYMS; do
  mv $VR/verify-$s.status $D/old/ 2>/dev/null; mv $OUT/e-2026-09-$s $D/old/validate-out-$s 2>/dev/null
  for f in /data/alpha/epochs/e-sep/root/$s-2026-09-*.binlog /data/tk037/roots/e-2026-09/$s-2026-09-*.binlog; do
    [ -e "$f" ] && [ ! -e "$VR/$(basename $f)" ] && ln -s "$f" "$VR/$(basename $f)"; done
done
unit() { local s=$1 rc1 rc2
  $V lob verify --symbol $s --root $VR > $LOGD/e-2026-09-$s.tk042.log 2>&1; rc1=$?
  ls $VR/$s-20*.binlog > $OUT/e-2026-09-$s.list
  $W lob validate --list $OUT/e-2026-09-$s.list --out-dir $OUT/e-2026-09-$s --threads 1 > $LOGD/e-2026-09-$s.tk042.validate.log 2>&1; rc2=$?
  echo "e-2026-09 $s rc=$rc1 validate_rc=$rc2" >> $D/b.log; }
export -f unit; export V W VR OUT LOGD D
echo $SYMS | tr ' ' '\n' | xargs -P 3 -I{} bash -c 'unit {}'
echo "C $(date +%T)" >> $D/steps.log
mkdir -p $D/ep; rm -f $D/ep/*.list
awk -F, 'NR>1 && $6=="only-raw" && $7=="ok" && $8=="not-run"{split($10,p,"/"); print p[5]"-"$1, $10}' $CSV | while read k f; do echo $f >> $D/ep/$k.list; done
unitc() { local k=$1; $W lob validate --list $D/ep/$k.list --out-dir $OUT/$k --threads 1 > $LOGD/$k.tk042.validate.log 2>&1; echo "$k validate_rc=$?" >> $D/c.log; }
export -f unitc
ls $D/ep | sed 's/\.list$//' | xargs -P ${PC:-6} -I{} bash -c 'unitc {}'
touch $D/ALL_DONE
