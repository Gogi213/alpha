#!/bin/bash
# tk065-g1ab.sh: A/B на сутках 10.03 (G1, флаги выкл.): повтор r2m-a (детерминизм) и alpha-93287fd-flag (прежний принятый) — одна ли разница с b14 в p07a-h2-fr3.
# Выход /data/tk065/g1ab.res (последняя строка done). g1-day.sh пишет в g1/<сутки> — подменяем каталог через копию скрипта.
d=2026-03-10; R=/data/tk065/g1ab.res; : > $R
for v in "r2m-a ab1" "alpha-93287fd-flag ab2"; do set -- $v
  sed "s#S=/data/tk065/g1/\$d#S=/data/tk065/g1/\$d-$2#" /data/tk065/g1-day.sh > /data/tk065/g1-day-$2.sh
  bash /data/tk065/g1-day-$2.sh $d $1
  S=/data/tk065/g1/$d-$2
  echo "$2 $1: $(tr '\n' ' ' < $S/res.txt)" >> $R
  f=b5/p07a-h2-fr3/$d/t-bid-btc4h-q1/signals.csv
  echo "$2 cell: $(diff $S/$f /data/tk048/tk040-b14/mar/$f | grep -c '^<')" >> $R
done
echo done >> $R
