#!/bin/bash
# tk048-strace-d15.sh (TK-053, запрос Исследователя 12:55): ОДНА группа суток 2026-01-15 (k=0 из G=8) тем же бинарником/окружением, что волна chain83, под strace -f -y (openat/read/pread64/mmap), холодный кэш; свод по классам файлов × диск. Запуск: systemd-run … /data/benchrun.sh stand bash /data/tk048/tk048-strace-d15.sh ; выход /data/tk048/strace-d15.txt, маркер strace-d15.done.
d=2026-01-15; MON=jan; k=0; G=8; bin=alpha-b18pgoflag
S0=/data/tk048/strace-d15; S=$S0/$MON; R=$S0.out; SH=/dev/shm/strace-d15; AB=/dev/shm/abin-d15
E=/data/tk046/$MON/home/alpha/epochs/e-$MON
OUT=/data/tk048/strace-d15.txt; rm -f /data/tk048/strace-d15.done $OUT
rm -rf $S0 $R $SH $AB; mkdir -p $S/bin $R $SH/b5 $SH/g $AB/study/approaches/D20
cp -aL /data/tk048/abin-t46m/study/approaches/D20/$d $AB/study/approaches/D20/
ln -s $SH/b5 $S/b5; ln -s $SH/g $S/g
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
sed 's#/usr/bin/time -f "%e %U %S %M" -o $R/t-$d-$k.txt bash $W/A.sh#strace -f -y -s 0 -e trace=openat,read,pread64,mmap -o $R/strace-raw.txt bash $W/A.sh#' /data/tk048/tk048-orch-job3m.sh > /data/tk048/tk048-orch-job3m-strace.sh
grep -q "strace -f" /data/tk048/tk048-orch-job3m-strace.sh || { echo "ABORT подмена не сработала" > $OUT; touch /data/tk048/strace-d15.done; exit 1; }
sync; echo 3 > /proc/sys/vm/drop_caches
rd() { awk -v D=$1 '$3==D{print $6}' /proc/diskstats; }
b0=$(rd sdb); a0=$(rd sda); t0=$(date +%s.%N)
ALPHA_APPROACH_BIN_DIR=$AB ALPHA_SKIP_SAME=1 EVENTS_WIDE=1 MON=$MON bash /data/tk048/tk048-orch-job3m-strace.sh $S $R $d $k $G
b1=$(rd sdb); a1=$(rd sda); t1=$(date +%s.%N)
{ echo "wall_s $(echo "$t1 - $t0" | bc) host_read_MB sdb $(( (b1-b0)/2048 )) sda $(( (a1-a0)/2048 )) (кэш холодный, под strace)"; ls -l $R/strace-raw.txt; } >> $OUT
python3 /data/tk048/tk048-strace-sum.py $R/strace-raw.txt >> $OUT
rm -rf $S0 $SH $AB
touch /data/tk048/strace-d15.done
