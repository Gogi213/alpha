#!/bin/bash
# tk065-g1bis3.sh: G1 10.03 (флаги выкл.) на plain-сборке b976c31 (до R2): красная ⇒ расхождение не от R2. Выход /data/tk065/g1bis3.res (последняя строка done).
d=2026-03-10; R=/data/tk065/g1bis3.res; : > $R; n=alpha-b976c31-v3
sed "s#S=/data/tk065/g1/\$d#S=/data/tk065/g1/\$d-b$n#" /data/tk065/g1-day.sh > /data/tk065/g1-day-b$n.sh
bash /data/tk065/g1-day-b$n.sh $d $n
S=/data/tk065/g1/$d-b$n; f=b5/p07a-h2-fr3/$d/t-bid-btc4h-q1/signals.csv
echo "$n: $(tr '\n' ' ' < $S/res.txt)" >> $R
echo "$n cell: $(diff $S/$f /data/tk048/tk040-b14/mar/$f | grep -c '^<')" >> $R
echo done >> $R
