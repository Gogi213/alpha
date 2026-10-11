#!/bin/bash
# tk065-g3.sh: G3(а) — кандидат R2 (флаги вкл.) на сутках 10.03, r2 и r2t, побайтно против выхода волны w-/wt-2026-03-10. Результат /data/tk065/g3.res (последняя строка done).
d=2026-03-10; B=alpha-93287fd-flag; : > /data/tk065/g3.res
SCR=r2 bash /data/tk065/probe.sh g3-$d $B mar $d
SCR=r2t bash /data/tk065/probe.sh g3t-$d $B mar $d
for p in "g3-$d w-$d" "g3t-$d wt-$d"; do set -- $p
  diff -rq /data/tk065/$1/b5 /data/tk065/$2/b5 > /data/tk065/$1.diff 2>&1
  echo "$1 vs $2: diff_lines $(wc -l < /data/tk065/$1.diff) files $(find /data/tk065/$1/b5 -type f | wc -l) $(tail -1 /data/tk065/$1/t.txt)" >> /data/tk065/g3.res
done
echo done >> /data/tk065/g3.res
