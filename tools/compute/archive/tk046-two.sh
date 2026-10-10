#!/bin/bash
# tk046-two.sh: янв+фев одним прогоном (59 суток, P=15, ra 65536) на готовых домах; прежние результаты месяцев — b5-solo (для diff -r).
# Метрики: /data/tk046/two/metrics.txt. Запуск: systemd-run --unit=tk046-two --property=CPUQuota=1500% bash tk046-two.sh
B=/opt/alpha-compute/bin; M=/data/tk046/two; mkdir -p $M /data/progress; rm -f $M/.run_done
for mon in jan feb; do E=/data/tk046/$mon/home/alpha/epochs/e-$mon
  [ -e $E/b5-solo ] || mv $E/b5 $E/b5-solo; mkdir -p $E/b5; done
for mon in jan feb; do sed "s#^#$mon #" /data/tk046/$mon/units.txt; done > $M/units.txt
dev=$(basename $(findmnt -no SOURCE -T /data)); RA=/sys/block/$dev/queue/read_ahead_kb
ra0=$(cat $RA 2>/dev/null); [ -w "$RA" ] && echo 65536 > $RA
cpu0=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $5}' /proc/stat); rd0=$(awk -v d=$dev '$3==d{print $6}' /proc/diskstats)
c0=$(systemctl show -p CPUUsageNSec --value tk046-two.service); t0=$(date +%s)
( while [ ! -e $M/.run_done ]; do n=$(ls /data/tk046/jan/home/alpha/epochs/e-jan/b5/.day-done-* /data/tk046/feb/home/alpha/epochs/e-feb/b5/.day-done-* 2>/dev/null | wc -l)
  echo "{\"ticket\":\"TK-046\",\"step\":\"янв+фев: счёт суток\",\"done\":$n,\"total\":$(wc -l < $M/units.txt),\"unit\":\"суток\",\"next\":\"сверка и отчёт\",\"updated\":\"$(date -Is)\"}" > /data/progress/tk046-two.json.tmp && mv /data/progress/tk046-two.json.tmp /data/progress/tk046-two.json; sleep 20; done ) &
xargs -a $M/units.txt -P 15 -L1 bash -c 'MON=$0 bash '$B'/tk046-day.sh $1'
t1=$(date +%s); c1=$(systemctl show -p CPUUsageNSec --value tk046-two.service); cpu1=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $5}' /proc/stat); rd1=$(awk -v d=$dev '$3==d{print $6}' /proc/diskstats)
[ -n "$ra0" ] && [ -w "$RA" ] && echo $ra0 > $RA
python3 - <<PY > $M/metrics.txt
a=[float(x) for x in "$cpu0".split()]; b=[float(x) for x in "$cpu1".split()]; w=$t1-$t0
print("wall_s",w); print("cpu_s_unit",($c1-$c0)/1e9); print("iowait_pct",100*(b[1]-a[1])/(b[0]-a[0])); print("disk_MBps",($rd1-$rd0)*512/1e6/w)
print("units",$(wc -l < $M/units.txt),"ra0",$ra0)
PY
touch $M/.run_done; wait
