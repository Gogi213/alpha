#!/bin/bash
# TK-048 A/B (I) шаг 1: carry-root на sda — копия фермы carry-root волн, ссылки sdb переадресованы на sda-копии D+1 (/alpha-sda/tk048/carry/<мес>/).
# Выход: /data/tk048/carry-sda/<мес>/root, отчёт /data/tk048/carry-sda/report.txt. Оригиналы не трогает.
O=/data/tk048/carry-sda; mkdir -p $O; : > $O/report.txt
for M in jan feb; do
  S=/data/tk046/$M/home/alpha/epochs/e-$M/root; D=$O/$M/root
  rm -rf $D; mkdir -p $D; cp -a $S/. $D/
  re=0; keep_sdb=0; sda=0
  while IFS= read -r -d '' l; do
    t=$(readlink "$l"); b=$(basename "$l")
    case $t in
      /alpha-sda/*) sda=$((sda+1));;
      *) c=/alpha-sda/tk048/carry/$M/$b
         if [ -e "$c" ]; then ln -sfn "$c" "$l"; re=$((re+1)); else keep_sdb=$((keep_sdb+1)); fi;;
    esac
  done < <(find $D -maxdepth 1 -type l -print0)
  echo "$M: sda_already $sda repointed $re left_on_sdb $keep_sdb" >> $O/report.txt
done
touch $O/done
