#!/bin/bash
# TK-071 п.3: 12 окон серии уже в очереди (FIFO по t_submit сохраняет чередование L/E); ждём все, гасим нагрузку, пишем summary.txt и done.
D=/data/tk071/series; S=/data/sched
ids=$(awk '$1!="load"{print $2}' $D/ids.txt)
while :; do
  n=0; for i in $ids; do grep -q '"state": "done"' $S/jobs/$i.json && n=$((n+1)); done
  [ $n -ge 12 ] && break; sleep 20
done
touch $D/stop
python3 /data/tk071/series-summary.py > $D/summary.txt 2>&1
touch $D/done
