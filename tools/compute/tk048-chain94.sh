#!/bin/bash
# chain94 (К-10, TK-048, Судья 20:09): ОДИН холодный прогон янв+фев (59 суток, P=20, G=8, alpha-b23pgoflag, abin в шм, sample3 — как chain91 = 634,6 с) с Y12 = 241 + 2650 добавок вразброс на сутки
# (генератор tk048-k10-gen.py, добавки как в chain81w). Прогноз ДО старта: 634,6·(625,9/211,8) ≈ 1 875 с. Ждёт, пока на сервере нет tk0s-*/tk115-* (чужие тяжёлые) и /dev/shm свободен (< 8 ГБ).
# job3mx = job3m + JDIR_ROOT/<мес> для скриптов суток + удаление scratch g/<сутки>-* после склейки и хвоста B (шм 32 ГБ не вместит g по 59 суткам; выход b5 тот же).
# Запуск: systemd-run --unit tk048-chain94 --collect bash /data/tk048/chain94.sh ; выход chain94.txt, маркер chain94.done.
rm -f /data/tk048/chain94.done /data/tk048/chain94.txt
OUT=/data/tk048/chain94.txt
for i in $(seq 1 540); do
  busy=$(systemctl list-units --state=running --no-legend 'tk0s-*' 'tk115-*' 'tk064-*' 'tk065-*' 2>/dev/null | wc -l); shm=$(df --output=used -m /dev/shm | tail -1)
  [ "$busy" -eq 0 ] && [ "$shm" -lt 8000 ] && break; sleep 10
done
echo "start_wait_done $(date +%T) busy $busy shm_used_MB $shm" >> $OUT
[ "$busy" -eq 0 ] && [ "$shm" -lt 8000 ] || { echo "ABORT занято: busy $busy shm $shm" >> $OUT; touch /data/tk048/chain94.done; exit 1; }
rm -rf /data/tk048/k10; mkdir /data/tk048/k10
DAYS=$(cat /data/tk046/jan/units.txt /data/tk046/feb/units.txt | tr '\n' ','); DAYS=${DAYS%,}
python3 /data/tk048/tk048-k10-gen.py /data/tk046 /data/tk048/k10 "$DAYS" > /data/tk048/k10/gen.log 2>&1 || { echo "ABORT генератор" >> $OUT; touch /data/tk048/chain94.done; exit 1; }
echo "gen_ok jan $(ls /data/tk048/k10/jan/*.txt | wc -l) feb $(ls /data/tk048/k10/feb/*.txt | wc -l) cells_d05 $(sort -u /data/tk048/k10/feb/jall-feb-2026-02-05.txt | wc -l)" >> $OUT
sed 's#^J=\$H/alpha/tmp-p07/cells-by-day/jall-\$MON-\$d.sh;#J=${JDIR_ROOT:-$H/alpha/tmp-p07/cells-by-day}${JDIR_ROOT:+/$MON}/jall-$MON-$d.sh;#' /data/tk048/tk048-orch-job3m.sh > /data/tk048/tk048-orch-job3mx.sh
echo 'rm -rf $S/g/$d-*' >> /data/tk048/tk048-orch-job3mx.sh
grep -q JDIR_ROOT /data/tk048/tk048-orch-job3mx.sh || { echo "ABORT патч job3mx" >> $OUT; touch /data/tk048/chain94.done; exit 1; }
SHA=/dev/shm/abin-t46m94
rm -rf $SHA
t0=$(date +%s)
nice -n 19 ionice -c3 cp -aL /data/tk048/abin-t46m $SHA
n=$(find $SHA -type f | wc -l); smb=$(du -sm $SHA | cut -f1)
{ echo "prep_copy_s $(( $(date +%s)-t0 ))"; echo "shm_n_files $n shm_mb $smb"; } >> $OUT
if [ "$n" -lt 5000 ] || [ "$smb" -lt 2000 ] || [ "$smb" -gt 9000 ]; then echo "ABORT копия abin в шм не прошла защиту" >> $OUT; rm -rf $SHA; touch /data/tk048/chain94.done; exit 1; fi
sed 's#/data/tk048/tk048-sample.sh#/data/tk048/tk048-sample3.sh#' /data/tk048/tk048-orch-grp16t.sh > /data/tk048/tk048-orch-grp16t-s3e.sh
grep -q tk048-sample3.sh /data/tk048/tk048-orch-grp16t-s3e.sh || { echo "ABORT подмена сэмплера" >> $OUT; touch /data/tk048/chain94.done; exit 1; }
cat > /data/tk048/chain94-inner.sh <<'IN'
#!/bin/bash
OUT=/data/tk048/chain94.txt
( while sleep 5; do echo "$(date +%s) $(systemctl show tk048-c94 -p MemoryCurrent --value 2>/dev/null) $(df --output=used /dev/shm | tail -1)"; done > /data/tk048/chain94-mem.txt ) & MS=$!
systemd-run --wait --collect --unit tk048-c94 -p CPUQuota=1500% -p MemoryAccounting=yes --setenv=JDIR_ROOT=/data/tk048/k10 --setenv=ALPHA_APPROACH_BIN_DIR=/dev/shm/abin-t46m94 --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=PFLIST=/data/tk048/pflist-jf59.txt --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3mx.sh bash /data/tk048/tk048-orch-grp16t-s3e.sh c94 alpha-b23pgoflag 8 20 > /dev/null 2>&1
kill $MS 2>/dev/null
{ echo "== c94 Y12 янв+фев alpha-b23pgoflag shm-abin + sample3"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0|read_GB|rchar|cpu|user|maxrss" /data/tk048/orch-c94.out/metrics.txt; cat /data/tk048/orch-c94.out/fail.txt 2>/dev/null; awk '{if($2>m)m=$2; if($3>s)s=$3} END{print "mem_unit_peak_MB",int(m/1048576),"shm_used_peak_MB",int(s/1024)}' /data/tk048/chain94-mem.txt; } >> $OUT
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain94-inner.sh > /dev/null 2>&1
rm -rf $SHA
touch /data/tk048/chain94.done
