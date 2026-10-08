#!/bin/bash
# TK-084 п.2 (В-198): окна verify по новым суткам XAUUSDT/CLUSDT (мар…окт) и гейт В-177 с ЗАМОРОЖЕННЫМИ порогами базы (/data/tk044/run/w-*.csv, как final3) -> /data/tk084/v171c/{run,g-*}. Маркер v171c-gate.done.
# Запуск: python3 /data/sched/alsched.py submit --cls prod --name tk084-v171c-gate --max-runtime 4h --cores 2 --mem 24 -- bash /data/tk084/tk084-v171c.sh
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root
V=/data/tk084/v171c; B=/opt/alpha-compute/bin; mkdir -p $V/run; cd $V/run || exit 2; rm -f /data/tk084/v171c-gate.done
for m in 03 04 05 06 07 08 09 10; do for s in XAUUSDT CLUSDT; do echo "$m $s"; done; done | xargs -P 2 -L1 bash -c '
  f=w-e-2026-$0-$1.csv; [ -s $f ] && exit 0
  /opt/alpha-compute/bin/alpha-tk044c lob verify --symbol $1 --root /data/tk037/roots/e-2026-$0 --keep-going --windows-out $f.tmp > w-e-2026-$0-$1.out 2>&1
  [ -s $f.tmp ] && mv $f.tmp $f || echo "FAIL $0 $1" >> failed.txt'
python3 $B/tk044-gate.py $V/g "/data/tk044/run/w-*.csv" --apply $V/run/w-*.csv > $V/gate.out 2> $V/gate.err
echo rc=$? >> $V/gate.out
touch /data/tk084/v171c-gate.done
