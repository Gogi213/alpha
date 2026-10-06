#!/bin/bash
# tk046-month.sh <jan|feb>: месяц v171b целиком — D20 новых + свечи/σ → дом → скрипты суток (K1 + verdict-csv + merge) → счёт P=15, ra 65536 (калибровка августа).
# Метрики этапа счёта: /data/tk046/<мес>/metrics.txt. Запуск: systemd-run --unit=tk046-<мес> --property=CPUQuota=1500% bash tk046-month.sh <мес>
MON=$1; export MON; YM=$(python3 -c "print({'jan':'2026-01','feb':'2026-02','mar':'2026-03','apr':'2026-04','may':'2026-05','jun':'2026-06','jul':'2026-07','aug':'2026-08','sep':'2026-09','oct':'2026-10'}['$MON'])"); B=/opt/alpha-compute/bin; M=/data/tk046/$MON
SINCE=$(date -d "$YM-01 -1 day" +%F); UNTIL=$(date -d "$YM-01 +1 month -1 day" +%F); export SINCE UNTIL
mkdir -p $M /data/progress; w0=$(date +%s)
if [ -z "${SKIP_PREP:-}" ]; then
python3 $B/tk046-mnew.py $MON > $M/mnew.txt || exit 3
( P=12 bash $B/tk046-derive.sh ) & ( bash $B/tk046-klines.sh ) & wait
echo "prep_wall_s=$(( $(date +%s) - w0 ))" > $M/prep.txt
[ -s $M/study/failed.txt ] && { echo "derive: ошибки"; exit 4; }
python3 $B/tk046-home.py /data/tk044/final3/verdict.csv $MON || exit 5
fi
[ -n "${PREP_ONLY:-}" ] && { touch $M/.prep_done; exit 0; }
H=$M/home; E=$H/alpha/epochs/e-$MON; mkdir -p $E/b5; for p in a1 a2 b c; do q=$H/alpha/epochs/e-aug/b5/p05-$p/2026-08-03; mkdir -p $q; ln -sf /home/deck/alpha/epochs/e-aug/b5/p05-$p/2026-08-03/manifest.txt $q/; done
( cd $E && HOME=$H python3 /home/deck/alpha/bin/p07-all-month.py $MON ${GEN_DAYS:+--days $GEN_DAYS} --merge --bin alpha-tk044k1-new > $M/gen.log 2>&1 ) || exit 6
sed -i 's#lob bounce-grid #lob bounce-grid --verdict-csv /data/tk044/final3/verdict.csv #' $H/alpha/tmp-p07/cells-by-day/jall-$MON-*.sh
ls $H/alpha/tmp-p07/cells-by-day/jall-$MON-*.sh | sed "s#.*jall-$MON-##; s#\.sh##" > $M/units.txt
[ -n "${GEN_ONLY:-}" ] && { touch $M/.gen_done; exit 0; }
dev=$(basename $(findmnt -no SOURCE -T /data)); RA=/sys/block/$dev/queue/read_ahead_kb
ra0=$(cat $RA 2>/dev/null); [ -w "$RA" ] && echo 65536 > $RA
cpu0=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $5}' /proc/stat); rd0=$(awk -v d=$dev '$3==d{print $6}' /proc/diskstats)
c0=$(systemctl show -p CPUUsageNSec --value tk046-$MON.service); t0=$(date +%s)
( while [ ! -e $M/.run_done ]; do n=$(ls $E/b5/.day-done-* 2>/dev/null | wc -l)
  echo "{\"ticket\":\"TK-046\",\"step\":\"$MON: счёт суток\",\"done\":$n,\"total\":$(wc -l < $M/units.txt),\"unit\":\"суток\",\"next\":\"сверка и отчёт\",\"updated\":\"$(date -Is)\"}" > /data/progress/tk046-$MON.json.tmp && mv /data/progress/tk046-$MON.json.tmp /data/progress/tk046-$MON.json; sleep 20; done ) &
xargs -a $M/units.txt -P 15 -I{} bash $B/tk046-day.sh {}
t1=$(date +%s); c1=$(systemctl show -p CPUUsageNSec --value tk046-$MON.service); cpu1=$(awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $6, $5}' /proc/stat); rd1=$(awk -v d=$dev '$3==d{print $6}' /proc/diskstats)
[ -n "$ra0" ] && [ -w "$RA" ] && echo $ra0 > $RA
python3 - <<PY > $M/metrics.txt
a=[float(x) for x in "$cpu0".split()]; b=[float(x) for x in "$cpu1".split()]; w=$t1-$t0
print("wall_s",w); print("cpu_s_unit",($c1-$c0)/1e9); print("iowait_pct",100*(b[1]-a[1])/(b[0]-a[0])); print("disk_MBps",($rd1-$rd0)*512/1e6/w)
print("units",$(wc -l < $M/units.txt),"ra0",$ra0)
PY
touch $M/.run_done; wait
