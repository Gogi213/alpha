#!/bin/bash
# tk048-orch-base.sh <метка> <бинарник> <сутки,сутки,...> [P]: база оркестровки — целые сутки января одним заданием, P параллельно, холодный кэш.
# Метрики: стена, ЦП юнита, iowait, диск sdb/sda, хвост (последний финиш − предпоследний). Вывод /data/tk048/orch-<метка>.out, гейт diff против b5 эталона.
lab=$1; bin=$2; days=${3//,/ }; P=${4:-15}; MON=jan
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; S=/data/tk048/orch-$lab; R=$S.out
rm -rf $S $R; mkdir -p $S/b5 $R
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
mkdir $S/bin; ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
export HOME=$H; cd $S || exit 2
sync; echo 3 > /proc/sys/vm/drop_caches
cpu0=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $2+$3+$4+$7+$8}' /proc/stat); rd0=$(awk '$3=="sdb"{print $6}' /proc/diskstats); ra0=$(awk '$3=="sda"{print $6}' /proc/diskstats)
U=tk048-$lab.service; c0=$(systemctl show -p CPUUsageNSec --value $U); t0=$(date +%s.%N)
for d in $days; do echo $d; done | xargs -P $P -I{} bash -c 'd={}; bash '$H'/alpha/tmp-p07/cells-by-day/jall-'$MON'-$d.sh > '$R'/day-$d.out 2> '$R'/day-$d.err; echo "$d $(date +%s.%N)" >> '$R'/finish.txt'
t1=$(date +%s.%N); c1=$(systemctl show -p CPUUsageNSec --value $U); cpu1=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $2+$3+$4+$7+$8}' /proc/stat); rd1=$(awk '$3=="sdb"{print $6}' /proc/diskstats); ra1=$(awk '$3=="sda"{print $6}' /proc/diskstats)
python3 - <<PY > $R/metrics.txt
a="$cpu0".split(); b="$cpu1".split(); w=$t1-$t0
f=sorted(float(l.split()[1]) for l in open("$R/finish.txt"))
print("wall_s",round(w,1)); print("iowait_pct",round(100*(float(b[1])-float(a[1]))/(float(b[0])-float(a[0])),1))
print("cpu_busy_machine_s",round((float(b[2])-float(a[2]))/100,1)); print("cpu_unit_s",round(($c1-$c0)/1e9,1)); print("core_util_pct_of_15",round(100*($c1-$c0)/1e9/(15*w),1)); print("disk_MBps_sdb",round(($rd1-$rd0)*512/1e6/w,1)); print("disk_MBps_sda",round(($ra1-$ra0)*512/1e6/w,1))
print("tail_s",round(f[-1]-f[-2],1) if len(f)>1 else 0, "first_finish_s",round(f[0]-$t0,1))
PY
bad=0; n=0; cd $S/b5; while IFS= read -r f; do n=$((n+1)); cmp -s "$f" "$E/b5/$f" || bad=$((bad+1)); done < <(find . -type f | grep -v "/\." )
echo "gate files $n diff $bad" >> $R/metrics.txt; touch $R/.done
