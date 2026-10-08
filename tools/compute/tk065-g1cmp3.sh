#!/bin/bash
# tk065-g1cmp3.sh: 10.03 флаги выкл.: выходы сборок R2 (r2m-a, r2m-trace, 93287fd, 4610ea34) побайтно против plain b976c31 (до R2). Выход /data/tk065/g1cmp3.res (последняя строка done).
cd /data/tk065/g1; R=/data/tk065/g1cmp3.res; : > $R
for v in wr2m-a wr2m-trace br2bis-93287fde br2bis-4610ea34 br2bis-c64c886d; do
  echo "$v: $(diff -rq 2026-03-10-balpha-b976c31-v3/b5 2026-03-10-$v/b5 2>&1 | wc -l)" >> $R; done
echo done >> $R
