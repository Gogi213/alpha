#!/bin/bash
# tk065-g1-day.sh <сутки марта> <бинарник-обёртка>: G1 TK-065 — флаги R2 выкл., ВСЕ клетки TK-040 суток (jall-mar-<сутки>.sh) побайтно против /data/tk048/tk040-b14/mar/b5.
# Выход: /data/tk065/g1/<сутки>/ (b5/, diff.txt, res.txt), маркер res.txt «done».
d=$1; B=${2:-alpha-93287fd-flag}; MON=mar; H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; REF=/data/tk048/tk040-b14/mar/b5; S=/data/tk065/g1/$d
rm -rf $S; mkdir -p $S/b5 $S/bin; ln -s /opt/alpha-compute/bin/$B $S/bin/alpha-tk044k1-new
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
grep -v '^cp b5/.cellstmp-.*[.]log /data' $H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh > $S/day.sh
t0=$(date +%s)
( export HOME=$H ALPHA_SKIP_SAME=1; cd $S; bash $S/day.sh > run.out 2> run.err; echo "rc $?" > $S/res.txt )
echo "wall_s $(( $(date +%s)-t0 ))" >> $S/res.txt
for s in $S/b5/*/; do sn=$(basename $s); [ -d $s/$d ] || continue; diff -rq $s/$d $REF/$sn/$d 2>&1 | sed "s#^#$sn: #"; done > $S/diff.txt
echo "diff_lines $(wc -l < $S/diff.txt) files $(find $S/b5 -type f -path "*/$d/*" | wc -l)" >> $S/res.txt
echo done >> $S/res.txt
