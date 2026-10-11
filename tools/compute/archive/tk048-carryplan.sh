#!/bin/bash
# TK-048 A/B (I): какие D+1 бинлоги пар pflist-jf59 лежат на sdb (цели ссылок carry-root), сколько байт. Только чтение, выход /data/tk048/carryplan/.
OUT=/data/tk048/carryplan; mkdir -p $OUT; rm -f $OUT/*; : > $OUT/need.txt
while read -r sy d; do
  n=$(date -u -d "$d +1 day" +%F); case ${d:5:2} in 01) MON=jan;; 02) MON=feb;; *) continue;; esac
  f=/data/tk046/$MON/home/alpha/epochs/e-$MON/root/$sy-$n.binlog
  [ -e "$f" ] || { echo "$sy $n MISSING" >> $OUT/missing.txt; continue; }
  t=$(readlink -f "$f"); sz=$(stat -c %s "$t")
  case "$t" in /alpha-sda/*) cl=sda;; *) cl=sdb;; esac
  echo "$cl $sz $sy $n $t" >> $OUT/need.txt
done < /data/tk048/pflist-jf59.txt
awk '{n[$1]++; b[$1]+=$2} END{for(k in n) printf "%s files %d GB %.2f\n",k,n[k],b[k]/1e9}' $OUT/need.txt > $OUT/summary.txt
echo done >> $OUT/summary.txt; touch $OUT/done
