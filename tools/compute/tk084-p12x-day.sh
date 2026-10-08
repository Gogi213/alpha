#!/bin/bash
# tk084-p12x-day.sh <сутки> <мес> <бинарник>: 9 клеток П-12 одних суток по XAU/CL (корень /data/tk084/x, verdict-xaucl.csv в jall) -> /data/tk084/wx-<сутки>/b5/p12/<сутки>. Как tk084-p12-day.sh, но свой корень и каталог.
d=$1; MON=$2; bin=${3:-alpha-tk084-dl}; H=/data/tk084/x/$MON/home; E=$H/alpha/epochs/e-$MON; S=/data/tk084/wx-$d; D=/data/tk084/daysx
rm -rf $S; mkdir -p $S/b5 $S/bin; ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
export HOME=$H ALPHA_SKIP_SAME=1; cd $S || exit 2
/usr/bin/time -f "%e %U %S %M" -o $S/t.txt bash $D/p12-$d.sh > $S/run.out 2> $S/run.err; rc=$?
exp=$(grep -c . $D/p12-$d.cells); got=$(cat $S/b5/p12/$d/*/forms.csv 2>/dev/null | awk -F, -v d=$d '$2==d{print $3}' | sort -u | grep -c .)
[ "$rc" = 0 ] && [ "$got" = "$exp" ] || { [ "$rc" = 0 ] && rc=97; echo "rc $rc cells $got/$exp" >> $S/t.txt; exit $rc; }
echo "rc 0 cells $got/$exp" >> $S/t.txt; touch /data/tk084/wx-$d.done
