#!/bin/bash
# tk065-g3a.sh: гейт правки семей A (6b792282, бинарник r2m-a): сутки 10.03, r2 и r2t против волны w-/wt-2026-03-10 (изменённые файлы по клеткам),
# затем G1 — флаги выкл. против b14 (tk065-g1-day.sh). Результат /data/tk065/g3a.res (последняя строка done).
d=2026-03-10; B=r2m-a; R=/data/tk065/g3a.res; : > $R
SCR=r2 bash /data/tk065/probe.sh g3a-$d $B mar $d
SCR=r2t bash /data/tk065/probe.sh g3at-$d $B mar $d
for p in "g3a-$d w-$d" "g3at-$d wt-$d"; do set -- $p
  diff -rq /data/tk065/$1/b5 /data/tk065/$2/b5 > /data/tk065/$1.diff 2>&1
  echo "$1 vs $2: diff_lines $(wc -l < /data/tk065/$1.diff) files $(find /data/tk065/$1/b5 -type f | wc -l) $(tail -1 /data/tk065/$1/t.txt)" >> $R
done
bash /data/tk065/g1-day.sh $d $B
echo "g1 $d: $(cat /data/tk065/g1/$d/res.txt | tr '\n' ' ')" >> $R
echo done >> $R
