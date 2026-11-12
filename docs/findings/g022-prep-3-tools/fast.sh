#!/bin/bash
# fast.sh OUT.jsonl.zst MAXTRIES cap.py-аргументы… — запускает cap.py с --abort-slow-ms 3000, пока соединение не окажется «быстрым» (код 3 = медленный маршрут: лаг > 3 с на 5–15 с). Пишет в OUT.tries число попыток.
out=$1; tries=$2; shift 2
n=0
while [ $n -lt $tries ]; do
  n=$((n+1))
  nice -n -10 /root/bnws-venv/bin/python /data/htx-probe/g022-prep-3-tools/cap.py --out "$out" --abort-slow-ms 3000 --tcp-probe "$@"
  rc=$?
  echo "$n $rc" >> "$out.tries"
  [ $rc != 3 ] && break
done
