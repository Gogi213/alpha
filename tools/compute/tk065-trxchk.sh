#!/bin/bash
# tk065 диагностика: TRX 15.02 одной монетой — команда jall (как в TK-040) и r2-команда волны; выход /data/tk065/chk.txt
MON=feb; d=2026-02-15; H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; B=alpha-b15pyr4pgoflag; T=$(date +%s)
for v in jall r2; do S=/data/tk065/chk-$v-$T; mkdir -p $S/b5 $S/bin $S/study/root-$d
  ln -s /opt/alpha-compute/bin/$B $S/bin/alpha-tk044k1-new
  for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|study|.day-*) continue;; esac; ln -s $x $S/$b; done
  for x in $E/study/*; do b=$(basename $x); case $b in root-*) continue;; esac; ln -s $x $S/study/$b; done
  for y in $E/study/root-$d/*; do [ "$(basename $y)" = instruments.csv ] || ln -s $y $S/study/root-$d/; done
  ( head -1 $E/study/root-$d/instruments.csv; grep '^TRXUSDT' $E/study/root-$d/instruments.csv ) > $S/study/root-$d/instruments.csv
  export HOME=$H ALPHA_SKIP_SAME=1; cd $S
  if [ $v = jall ]; then sed -n 1,3p $H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh | grep -v '^cp b5/.cellstmp' > A.sh; else grep -v '^cp b5/.cellstmp' /data/tk065/days/r2-$d.sh > A.sh; fi
  bash A.sh > run.out 2> run.err; echo "$v rc $? dir $S" >> /data/tk065/chk.txt
  for f in $(ls b5/.cellstmp-$d/*/forms.csv b5/*/$d/*/forms.csv 2>/dev/null | head -3); do echo "$v $f $(grep -c TRXUSDT $f) $(grep TRXUSDT $f | head -1 | cut -d, -f1-6)" >> /data/tk065/chk.txt; done
done
touch /data/tk065/chk.done
