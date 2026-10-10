#!/bin/bash
# tk048-rd.sh: скорость чтения sdb холодным кэшем при P параллельных читателях (каждый — файлы своих суток подряд, 40 с). Выход /data/tk048/rd.txt
R=/data/tk046/jan/home/alpha/epochs/e-jan/root; out=/data/tk048/rd.txt; : > $out
echo "ra_kb $(cat /sys/block/sdb/queue/read_ahead_kb)" >> $out
for P in 1 4 8 15; do
  sync; echo 3 > /proc/sys/vm/drop_caches
  a=$(awk '$3=="sdb"{print $6}' /proc/diskstats); t0=$(date +%s.%N)
  for i in $(seq 1 $P); do d=$(printf "2026-01-%02d" $((i+2)))
    ( ls $R | grep -- "-$d.binlog" | while read f; do cat $R/$f > /dev/null; done ) &
  done
  sleep 40; pkill -x cat; kill $(jobs -p) 2>/dev/null; wait 2>/dev/null
  b=$(awk '$3=="sdb"{print $6}' /proc/diskstats); t1=$(date +%s.%N)
  python3 -c "print('P=$P MB/s', round(($b-$a)*512/1e6/($t1-$t0),1))" >> $out
done
touch /data/tk048/rd.done
