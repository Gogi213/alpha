#!/bin/bash
# tk065-g1ab2.sh: G1 10.03 флаги выкл. с r2m-trace (94af6f89, до правки A): отличие p07a-h2-fr3 от b14 было ли до правки. Выход /data/tk065/g1ab2.res (последняя строка done).
d=2026-03-10; R=/data/tk065/g1ab2.res; : > $R
sed "s#S=/data/tk065/g1/\$d#S=/data/tk065/g1/\$d-ab3#" /data/tk065/g1-day.sh > /data/tk065/g1-day-ab3.sh
bash /data/tk065/g1-day-ab3.sh $d r2m-trace
S=/data/tk065/g1/$d-ab3; f=b5/p07a-h2-fr3/$d/t-bid-btc4h-q1/signals.csv
echo "ab3 r2m-trace: $(tr '\n' ' ' < $S/res.txt)" >> $R
echo "ab3 cell: $(diff $S/$f /data/tk048/tk040-b14/mar/$f | grep -c '^<')" >> $R
echo done >> $R
