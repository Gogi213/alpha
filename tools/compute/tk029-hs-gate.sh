#!/bin/bash
# TK-029: гейт байт-в-байт + ЦП: alpha-tk029-merge-v3 (old) против alpha-tk029-hs-v3 (new) на сутках янв. Пара идёт параллельно (одна нагрузка).
# tk029-hs-gate.sh "01 02 03"
export HOME=/home/deck; A=$HOME/alpha; E=$A/epochs/e-jan; G=$A/tk029gate; O=$G/out; mkdir -p $O; : > $O/summary.txt
for v in old new; do mkdir -p $G/$v; ln -sfn $A/bin $G/$v/bin; ln -sfn $E/study $G/$v/study; ln -sfn $E/root $G/$v/root; mkdir -p $G/$v/b5; done
run() { # $1=old|new $2=день
  local v=$1 d=$2 b=alpha-tk029-merge-v3; [ $v = new ] && b=alpha-tk029-hs-v3
  sed -e "s#bin/alpha-tk029-merge-v3#bin/$b#" -e '/grid.log/d' $A/tmp-p07/cells-by-day/jall-jan-2026-01-$d.sh > $G/$v/day-$d.sh
  cd $G/$v; rm -rf b5/.cellstmp-2026-01-$d b5/p07*/2026-01-$d
  /usr/bin/time -f "$v $d wall=%e user=%U sys=%S maxrss_kb=%M" -o $O/time-$v-$d.txt bash day-$d.sh > $O/$v-$d.out 2> $O/$v-$d.err; echo $? > $O/$v-$d.rc
}
for d in $1; do
  run old $d & run new $d & wait
  for v in old new; do ( cd $G/$v; find b5 -path "*2026-01-$d*" -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done > $O/sha-$v-$d.txt ); done
  { cat $O/time-old-$d.txt $O/time-new-$d.txt; echo "rc old=$(cat $O/old-$d.rc) new=$(cat $O/new-$d.rc) files=$(wc -l < $O/sha-old-$d.txt)"; cmp $O/sha-old-$d.txt $O/sha-new-$d.txt && echo "GATE $d OK" || echo "GATE $d DIFF"; } >> $O/summary.txt
done
date > $G/DONE
