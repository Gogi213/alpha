#!/bin/bash
# сверка прогона янв+фев (b5) с отдельными прогонами (b5-solo), без точечных файлов; результат — /data/tk046/two/diff.txt, маркер .diff_done
M=/data/tk046/two; : > $M/diff.txt
for m in jan feb; do E=/data/tk046/$m/home/alpha/epochs/e-$m
  echo "$m: файлов $(cd $E/b5 && find . -type f ! -name '.*' ! -path '*/.*' | wc -l) / $(cd $E/b5-solo && find . -type f ! -name '.*' ! -path '*/.*' | wc -l)" >> $M/diff.txt
  diff -rq -x '.*' $E/b5 $E/b5-solo >> $M/diff.txt; echo "$m: diff rc=$?" >> $M/diff.txt; done
touch $M/.diff_done
