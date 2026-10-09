#!/bin/bash
# chain63 (способ 3, В-185): колоночные контейнеры (lob archive --columnar --level 19) всех бинлогов суток 2026-01-01 -> /data/tk048/col/; размер и время на файл, round-trip сверку делает сама команда. Выход /data/tk048/col/res.txt, маркер /data/tk048/chain63.done. Запуск: systemd-run … /data/tk052/benchrun2.sh stand bash chain63.sh
E=/data/tk046/jan/home/alpha/epochs/e-jan/study/root-2026-01-01; B=/opt/alpha-compute/bin/alpha-b15col; O=/data/tk048/col; P=${P:-4}
rm -rf $O /data/tk048/chain63.done; mkdir -p $O
ls $E/*.binlog | wc -l > $O/n.txt
t0=$(date +%s.%N)
ls $E/*.binlog | xargs -P $P -I{} sh -c 'f=$1; s=$(date +%s.%N); if $2 lob archive --path $f --columnar --level 19 --out '$O'/$(basename $f).zst > '$O'/$(basename $f).log 2>&1; then r=ok; else r=FAIL; fi; echo "$r $(stat -L -c %s $f) $(stat -c %s '$O'/$(basename $f).zst 2>/dev/null) $(echo "$(date +%s.%N)-$s" | bc) $(basename $f)"' _ {} $B > $O/per-file.txt
t1=$(date +%s.%N)
awk '{n++; a+=$2; b+=$3; if($1!="ok")f++} END{print "files",n,"fail",f+0,"orig",a,"col",b,"ratio",b/a}' $O/per-file.txt > $O/res.txt
echo "wall $(echo "$t1-$t0" | bc) P=$P" >> $O/res.txt
touch /data/tk048/chain63.done
