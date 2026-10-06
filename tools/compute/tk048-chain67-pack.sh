#!/bin/bash
# chain67 (способ 3, В-185): колоночные контейнеры (lob archive --columnar --level 19) бинлогов суток 2026-01-02..15 -> /data/tk048/col15/<сутки>/; сутки 01 уже в /data/tk048/col (chain63). Round-trip сверку делает сама команда.
# Подача: через alsched (prod, 4 ядра), см. tk048-chain67-submit.sh. Выход /data/tk048/col15/res.txt, маркер /data/tk048/chain67.done.
S=/data/tk046/jan/home/alpha/epochs/e-jan/study; B=/opt/alpha-compute/bin/alpha-b15col; O=/data/tk048/col15; P=${P:-4}
rm -f /data/tk048/chain67.done; mkdir -p $O; t0=$(date +%s.%N)
for n in 02 03 04 05 06 07 08 09 10 11 12 13 14 15; do
  d=2026-01-$n; mkdir -p $O/$d; [ -e $O/$d/.ok ] && continue
  ls $S/root-$d/*.binlog | xargs -P $P -I{} sh -c 'f=$1; o='$O/$d'/$(basename $f).zst; if $2 lob archive --path $f --columnar --level 19 --out $o > $o.log 2>&1; then r=ok; else r=FAIL; fi; echo "$r $(stat -L -c %s $f) $(stat -c %s $o 2>/dev/null) $(basename $f)"' _ {} $B > $O/$d/per-file.txt
  awk -v d=$d '{n++; a+=$2; b+=$3; if($1!="ok")f++} END{print d,"files",n,"fail",f+0,"orig",a,"col",b,"ratio",b/a}' $O/$d/per-file.txt | tee -a $O/res.txt
  grep -q '^FAIL' $O/$d/per-file.txt || touch $O/$d/.ok
done
echo "wall $(echo "$(date +%s.%N)-$t0" | bc) P=$P" >> $O/res.txt
touch /data/tk048/chain67.done
