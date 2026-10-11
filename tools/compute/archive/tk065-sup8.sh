#!/bin/bash
# tk065-sup8.sh [бинарник]: досчёт волны R2 на 8 сутках с монетами из досчёта TK-040 (/data/tk040/sup/<мес>/home); выход /data/tk065/ws-<сутки>, wst-<сутки>, маркер /data/tk065/sup8.done
B=${1:-alpha-b15pyr4pgoflag}; O=/data/tk065/days-sup; mkdir -p $O
for dm in 2026-02-15:feb 2026-02-16:feb 2026-03-01:mar 2026-03-02:mar 2026-04-23:apr 2026-07-24:jul 2026-08-06:aug 2026-08-11:aug; do
  d=${dm%%:*}; M=${dm##*:}; H=/data/tk040/sup/$M/home; E=$H/alpha/epochs/e-$M
  python3 /data/tk065/r2-gen.py $H/alpha/tmp-p07/cells-by-day/jall-$M-$d.sh $d $O > /dev/null || exit 3
  for pass in r2 r2t; do
    lab=ws-$d; [ $pass = r2t ] && lab=wst-$d; S=/data/tk065/$lab
    mkdir -p $S/b5 $S/bin; ln -sfn /opt/alpha-compute/bin/$B $S/bin/alpha-tk044k1-new
    for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -sfn $x $S/$b; done
    ( export HOME=$H ALPHA_SKIP_SAME=1; cd $S; /usr/bin/time -f "%e %U %S %M" -o $S/t.txt bash $O/$pass-$d.sh > $S/run.out 2> $S/run.err; echo "rc $?" >> $S/t.txt )
  done
done
touch /data/tk065/sup8.done
