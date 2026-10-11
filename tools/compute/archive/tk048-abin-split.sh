#!/bin/bash
# TK-048 07.10: зеркало каталога .abin с нечётными сутками на sda (/alpha-sda) — разнести мелкие файлы по двум шпинделям. Выход /data/tk048/abin-split.txt, маркер abin-split.done.
set -u
SRC=/data/tk048/abin-t46m/study/approaches/D20; DST=/data/tk048/abin-t46s; SDA=/alpha-sda/tk048/abin-t46s
rm -f /data/tk048/abin-split.done; rm -rf $DST $SDA; mkdir -p $DST/study/approaches/D20 $SDA
i=0; for d in $(ls $SRC | sort); do i=$((i+1))
  if [ $((i%2)) = 1 ]; then cp -a $SRC/$d $SDA/$d && ln -s $SDA/$d $DST/study/approaches/D20/$d; else ln -s $SRC/$d $DST/study/approaches/D20/$d; fi
done
{ echo "days $i"; echo "sda_days $(ls $SDA | wc -l)"; echo "sda_files $(ls $SDA/* | grep -vc :)"; echo "sda_bytes $(cat $SDA/*/* | wc -c)"; echo "src_files_d1 $(ls $SRC/2026-01-01 | wc -l) sda_files_d1 $(ls $SDA/2026-01-01 | wc -l)"; } > /data/tk048/abin-split.txt 2>&1
touch /data/tk048/abin-split.done
