#!/bin/bash
# tk065-g1cmp4.sh: какая 1 строка отличает выходы r2m-a и r2bis-93287fde от plain b976c31 на 10.03 (флаги выкл.). Выход /data/tk065/g1cmp4.res (последняя строка done).
cd /data/tk065/g1; R=/data/tk065/g1cmp4.res; : > $R
for v in wr2m-a br2bis-93287fde; do echo "== $v" >> $R; diff -rq 2026-03-10-balpha-b976c31-v3/b5 2026-03-10-$v/b5 >> $R 2>&1; done
echo done >> $R
