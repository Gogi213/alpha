#!/bin/bash
# tk065-g1bis.sh: бисекция G1 10.03 (флаги выкл.): r2bis-c64c886d и r2bis-4610ea34 параллельно; ячейка p07a-h2-fr3 UNI против b14. Выход /data/tk065/g1bis.res (последняя строка done).
d=2026-03-10; R=/data/tk065/g1bis.res; : > $R
run() { n=$1
  sed "s#S=/data/tk065/g1/\$d#S=/data/tk065/g1/\$d-b$n#" /data/tk065/g1-day.sh > /data/tk065/g1-day-b$n.sh
  bash /data/tk065/g1-day-b$n.sh $d $n; }
run r2bis-c64c886d & run r2bis-4610ea34 & wait
for n in r2bis-c64c886d r2bis-4610ea34; do S=/data/tk065/g1/$d-b$n; f=b5/p07a-h2-fr3/$d/t-bid-btc4h-q1/signals.csv
  echo "$n: $(tr '\n' ' ' < $S/res.txt)" >> $R
  echo "$n cell: $(diff $S/$f /data/tk048/tk040-b14/mar/$f | grep -c '^<')" >> $R; done
echo done >> $R
