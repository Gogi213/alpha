#!/bin/bash
# TK-048 A/B (I): копия D+1 бинлогов пар pflist-jf59, лежащих на sdb, на sda (cp + cmp; оригиналы остаются). Список /data/tk048/carryplan/need.txt (cl sz sym n target).
# Выход: /alpha-sda/tk048/carry/<мес>/, журнал /data/tk048/carrycopy/{log.txt,summary.txt}, маркер done.
L=/data/tk048/carrycopy; mkdir -p $L; : > $L/log.txt; rm -f $L/done $L/summary.txt
ok=0; bad=0; skip=0; bytes=0
while read -r cl sz sy n t; do
  [ "$cl" = sdb ] || continue
  case ${n:5:2} in 01) M=jan;; 02) M=feb;; *) M=other;; esac
  D=/alpha-sda/tk048/carry/$M; mkdir -p $D; dst=$D/$sy-$n.binlog
  if [ -e "$dst" ] && cmp -s "$t" "$dst"; then skip=$((skip+1)); continue; fi
  if nice -n 19 ionice -c3 cp --no-preserve=links "$t" "$dst.part" && cmp -s "$t" "$dst.part"; then mv "$dst.part" "$dst"; ok=$((ok+1)); bytes=$((bytes+sz)); else bad=$((bad+1)); echo "BAD $sy $n" >> $L/log.txt; rm -f "$dst.part"; fi
done < /data/tk048/carryplan/need.txt
echo "copied $ok skipped $skip bad $bad GB $(( bytes/1000000000 ))" > $L/summary.txt; touch $L/done
