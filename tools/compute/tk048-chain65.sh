#!/bin/bash
# chain65: цена чтения Reader — тот же файл (HYPE суток 01.01) из исходного бинлога, zstd-v1 (L9) и колоночного v2 (chain63): время `lob gaps` (полный декод кадров), 3 повтора, кэш горячий. Выход /data/tk048/chain65.txt, маркер chain65.done.
B=/opt/alpha-compute/bin/alpha-b15col; E=/data/tk046/jan/home/alpha/epochs/e-jan/study/root-2026-01-01; C=/data/tk048/col; O=/data/tk048/chain65; rm -rf $O /data/tk048/chain65.done; mkdir -p $O
f=HYPEUSDT-2026-01-01.binlog
$B lob archive --path $E/$f --level 9 --out $O/$f.v1.zst > /dev/null 2>&1
cat $E/$f $O/$f.v1.zst $C/$f.zst > /dev/null
{ ls -l $E/$f $O/$f.v1.zst $C/$f.zst | awk '{print $5,$9}'
for v in $E/$f $O/$f.v1.zst $C/$f.zst; do for i in 1 2 3; do /usr/bin/time -f "$(basename $v) %e s user %U sys %S rss_kb %M" $B lob gaps $v > /dev/null 2> $O/t.txt; tail -1 $O/t.txt; done; done; } > /data/tk048/chain65.txt 2>&1
touch /data/tk048/chain65.done
