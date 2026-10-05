#!/bin/bash
# tk048-orch-r1.sh <метка> <бинарник> <сутки,...> <G> [P]: малые единицы (сутки × группа из G подряд идущих символов пула), общая очередь xargs -P, холодный кэш.
# Метрики и гейт — как tk048-orch-base.sh. Вывод /data/tk048/orch-<метка>.out
lab=$1; bin=$2; days=${3//,/ }; G=$4; P=${5:-15}; MON=jan
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; S=/data/tk048/orch-$lab; R=$S.out
rm -rf $S $R; mkdir -p $S/b5 $S/g $R
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
mkdir $S/bin; ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
export HOME=$H; cd $S || exit 2
bash /data/tk048/tk048-tail.sh snap > $R/cg0.txt
sync; echo 3 > /proc/sys/vm/drop_caches
cpu0=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $2+$3+$4+$7+$8}' /proc/stat); rd0=$(awk '$3=="sdb"{print $6}' /proc/diskstats); ra0=$(awk '$3=="sda"{print $6}' /proc/diskstats)
bash /data/tk048/tk048-sample.sh $R/samples.tsv & SMP=$!; U=tk048-$lab.service; c0=$(systemctl show -p CPUUsageNSec --value $U); t0=$(date +%s.%N)
python3 /data/tk048/tk048-r1.py $S ${days// /,} $G ${READERS:-1} ${CACHE_GB:-22} 2> $R/r1.err | xargs -P $P -L1 bash -c 'bash /data/tk048/tk048-orch-job.sh '$S' '$R' $0 $1 '$G
kill $SMP 2>/dev/null; t1=$(date +%s.%N); c1=$(systemctl show -p CPUUsageNSec --value $U); cpu1=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $2+$3+$4+$7+$8}' /proc/stat); rd1=$(awk '$3=="sdb"{print $6}' /proc/diskstats); ra1=$(awk '$3=="sda"{print $6}' /proc/diskstats)
python3 - <<PY > $R/metrics.txt
a="$cpu0".split(); b="$cpu1".split(); w=$t1-$t0
f=sorted(float(l.split()[1]) for l in open("$R/finish.txt"))
print("wall_s",round(w,1)); print("iowait_pct",round(100*(float(b[1])-float(a[1]))/(float(b[0])-float(a[0])),1))
print("cpu_busy_machine_s",round((float(b[2])-float(a[2]))/100,1)); print("cpu_unit_s",round(($c1-$c0)/1e9,1)); print("core_util_pct_of_15",round(100*($c1-$c0)/1e9/(15*w),1))
print("disk_MBps_sdb",round(($rd1-$rd0)*512/1e6/w,1)); print("disk_MBps_sda",round(($ra1-$ra0)*512/1e6/w,1))
bz=(float(b[2])-float(a[2]))/100; un=($c1-$c0)/1e9; ip=100*(bz-un)/un
print("interference_pct",round(ip,1)); print("INVALID_INTERFERENCE" if ip>5 else "interference_ok")
print("tail_s",round(f[-1]-f[-2],1) if len(f)>1 else 0, "first_finish_s",round(f[0]-$t0,1), "G",$G)
PY
bash /data/tk048/tk048-tail.sh tail $R $U
[ -e $R/fail.txt ] && cat $R/fail.txt >> $R/metrics.txt
bad=0; n=0; cd $S/b5; while IFS= read -r f; do n=$((n+1)); cmp -s "$f" "$E/b5/$f" || bad=$((bad+1)); done < <(find . -type f | grep -v "/\." )
echo "gate files $n diff $bad" >> $R/metrics.txt; touch $R/.done
