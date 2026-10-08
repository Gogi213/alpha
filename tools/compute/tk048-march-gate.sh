#!/bin/bash
# TK-048: диагностика гейта марта — одни сутки A, 327 diff-строк против TK-040 откуда. Выход /data/tk048/march-gate.txt, маркер march-gate.done.
# Запуск: systemd-run --unit tk048-margate --collect /data/tk052/benchrun2.sh stand bash /data/tk048/march-gate.sh
A=${1:-alpha-b20-pgo}; d=${2:-2026-03-02}
G=/data/tk048/mgate; OUT=/data/tk048/march-gate.txt; MON=mar; H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; REF=/data/tk048/tk040-b14/mar/b5
rm -f /data/tk048/march-gate.done $OUT; rm -rf $G; mkdir -p $G/b5 $G/bin; S=$G
ln -s /opt/alpha-compute/bin/$A $S/bin/alpha-tk044k1-new
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
grep -v '^cp b5/.cellstmp-.*[.]log /data' $H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh > $S/day.sh
( export HOME=$H ALPHA_SKIP_SAME=1; cd $S; bash $S/day.sh > run.out 2> run.err; echo rc $? >> $OUT )
for s in $G/b5/*/; do sn=$(basename $s); [ -d $s/$d ] || continue; diff -rq $s/$d $REF/$sn/$d 2>&1 | sed "s#^#$sn: #"; done > $G/diff.txt
wc -l < $G/diff.txt >> $OUT; cut -d: -f1 $G/diff.txt | sort | uniq -c | sort -rn | head -10 >> $OUT
head -15 $G/diff.txt >> $OUT; grep -c "^.*Only in /data/tk048/tk040" $G/diff.txt >> $OUT
touch /data/tk048/march-gate.done
