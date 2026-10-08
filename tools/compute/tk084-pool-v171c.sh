#!/bin/bash
# TK-084 п.2: пул v171c = v171b + сутки XAUUSDT/CLUSDT; вердикт v171c = final3 + verdict-xaucl. Выход /data/tk084/v171c/{pool-v171c.csv,verdict-v171c.csv}, копия пула в /data/tk037.
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LC_ALL=C
V=/data/tk084/v171c; cd $V || exit 2
rm -f /data/tk084/v171c-pool.done
{ head -1 /data/tk037/pool-v171b.csv
  { tail -n +2 /data/tk037/pool-v171b.csv; awk -F, 'NR>1{print $1","$2",1"}' verdict-xaucl.csv; } | sort -t, -k2,2 -k1,1 -u
} > pool-v171c.csv
{ head -1 /data/tk044/final3/verdict.csv; { tail -n +2 /data/tk044/final3/verdict.csv; tail -n +2 verdict-xaucl.csv; } | sort -t, -k1,1 -k2,2 -u; } > verdict-v171c.csv
wc -l /data/tk037/pool-v171b.csv pool-v171c.csv /data/tk044/final3/verdict.csv verdict-v171c.csv > pool.out
cp pool-v171c.csv /data/tk037/pool-v171c.csv
sha256sum pool-v171c.csv verdict-v171c.csv >> pool.out
touch /data/tk084/v171c-pool.done
