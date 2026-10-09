#!/bin/bash
# chain61 (способ 1, В-185): zstd-контейнер существующих бинлогов суток 2026-01-01 — размер и время `lob archive` на уровнях 1/3/9 (40 крупнейших файлов), разжатие zstd -d. Выход /data/tk048/chain61/res.txt, маркер /data/tk048/chain61.done. Запуск: systemd-run … /data/tk052/benchrun2.sh stand bash chain61.sh
E=/data/tk046/jan/home/alpha/epochs/e-jan/study/root-2026-01-01; B=/opt/alpha-compute/bin/alpha-b14-pgo; O=/data/tk048/chain61
rm -rf $O /data/tk048/chain61.done; mkdir -p $O; R=$O/res.txt
for f in $E/*.binlog; do echo "$(stat -L -c %s $f) $f"; done | sort -rn | head -40 > $O/list.txt
awk '{s+=$1} END{print "files 40 bytes " s}' $O/list.txt > $R
cut -d' ' -f2 $O/list.txt | xargs cat > /dev/null
for L in 1 3 9; do
  mkdir -p $O/L$L; t0=$(date +%s.%N)
  cut -d' ' -f2 $O/list.txt | xargs -P 4 -I{} sh -c 'f=$1; $2 lob archive --path $f --level '$L' --out '$O'/L'$L'/$(basename $f).zst > /dev/null 2>&1' _ {} $B
  t1=$(date +%s.%N)
  sz=$(cat $O/L$L/*.zst | wc -c); n=$(ls $O/L$L | wc -l)
  echo "level $L files $n bytes $sz archive_wall $(echo "$t1-$t0" | bc)" >> $R
  t0=$(date +%s.%N); for f in $O/L$L/*.zst; do zstd -d -c --long=31 $f | wc -c; done | awk '{s+=$1} END{print s}' > $O/raw$L.txt; t1=$(date +%s.%N)
  echo "level $L unzstd_wall $(echo "$t1-$t0" | bc) raw $(cat $O/raw$L.txt)" >> $R
done
touch /data/tk048/chain61.done
