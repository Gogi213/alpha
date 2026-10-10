#!/bin/bash
# TK-048: один прогон B (alpha-b22-f1, сутки марта) с сохранением текста diff -rq против ref прошлого A/B (/data/tk048/mab2/ref). Запуск: systemd-run --unit tk048-marab3 --collect /data/tk052/benchrun2.sh stand bash /data/tk048/march-ab3.sh; выход /data/tk048/march-ab3.txt, маркер march-ab3.done.
B=alpha-b22-f1; d=2026-03-02
G=/data/tk048/mab2; OUT=/data/tk048/march-ab3.txt; MON=mar; H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; D=/data/tk048/march-ab3-diff.txt
rm -f /data/tk048/march-ab3.done $OUT; rm -rf $G/B9; mkdir -p $G/B9/b5 $G/B9/bin
S=$G/B9
ln -s /opt/alpha-compute/bin/$B $S/bin/alpha-tk044k1-new
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
grep -v '^cp b5/.cellstmp-.*[.]log /data' $H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh > $S/day.sh
( export HOME=$H ALPHA_SKIP_SAME=1; cd $S; t0=$(date +%s.%N); bash $S/day.sh > run.out 2> run.err; rc=$?; t1=$(date +%s.%N)
  echo "B9 $B $d rc $rc wall_s $(echo "$t1 - $t0" | bc)" >> $OUT )
diff -rq $S/b5 $G/ref > $D 2>&1
echo "diff -rq:" >> $OUT; cat $D >> $OUT
sed -n 's/^Files \(.*\) and \(.*\) differ$/\1 \2/p' $D | while read a b; do echo "== $a" >> $OUT; diff "$a" "$b" | head -6 >> $OUT; done
rm -rf $S/b5
touch /data/tk048/march-ab3.done
