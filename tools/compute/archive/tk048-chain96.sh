#!/bin/bash
# chain96 (TK-048, Э-17 Quant): ABBA янв+фев 59 суток, A = alpha-b24c0flag (база, C' выкл), B = alpha-e17p2flag (Э-17, профиль e17b переобучен на HEAD);
# Порядок B A A B; gate diff 0 к эталону — в metrics каждого прогона. После ABBA — сутки d15 на стенде со счётчиком отката (ALPHA_TICK_STATS=1).
# Запуск: systemd-run --unit tk048-chain96 --collect bash /data/tk048/chain96.sh ; выход /data/tk048/chain96.txt, маркер chain96.done.
rm -f /data/tk048/chain96.done /data/tk048/chain96.txt
OUT=/data/tk048/chain96.txt
SHA=/dev/shm/abin-t46m96
rm -rf $SHA
t0=$(date +%s)
nice -n 19 ionice -c3 cp -aL /data/tk048/abin-t46m $SHA
n=$(find $SHA -type f | wc -l); smb=$(du -sm $SHA | cut -f1)
{ echo "prep_copy_s $(( $(date +%s)-t0 ))"; echo "shm_n_files $n shm_mb $smb"; } >> $OUT
if [ "$n" -lt 5000 ] || [ "$smb" -lt 2000 ] || [ "$smb" -gt 9000 ]; then echo "ABORT копия abin в шм не прошла защиту" >> $OUT; rm -rf $SHA; touch /data/tk048/chain96.done; exit 1; fi
sed 's#/data/tk048/tk048-sample.sh#/data/tk048/tk048-sample3.sh#' /data/tk048/tk048-orch-grp16t.sh > /data/tk048/tk048-orch-grp16t-s3e.sh
grep -q tk048-sample3.sh /data/tk048/tk048-orch-grp16t-s3e.sh || { echo "ABORT подмена сэмплера не сработала" >> $OUT; touch /data/tk048/chain96.done; exit 1; }
cat > /data/tk048/chain96-inner.sh <<'IN'
#!/bin/bash
OUT=/data/tk048/chain96.txt
for arm in "c96b alpha-e17p2flag" "c96a alpha-b24c0flag" "c96a2 alpha-b24c0flag" "c96b2 alpha-e17p2flag"; do set -- $arm
  systemd-run --wait --collect --unit tk048-$1 -p CPUQuota=1500% --setenv=ALPHA_APPROACH_BIN_DIR=/dev/shm/abin-t46m96 --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=PFLIST=/data/tk048/pflist-jf59.txt --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3m.sh bash /data/tk048/tk048-orch-grp16t-s3e.sh $1 $2 8 20 > /dev/null 2>&1
  { echo "== $1 $2"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0|read_GB|rchar|user_s|cpu" /data/tk048/orch-$1.out/metrics.txt; } >> $OUT
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain96-inner.sh > /dev/null 2>&1
/data/benchrun.sh stand bash /data/tk051/stand.sh alpha-e17b-pgo d15 ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1 ALPHA_TICK_STATS=1 TAG=t96s > /data/tk048/chain96-stand.out 2>&1
grep -h "тик/лот" /data/tk051/stand-out/t96s-d15-alpha-e17b-pgo/grid.log | tail -1 > /data/tk048/chain96-ticks.txt; cp /data/tk051/stand-out/t96s-d15-alpha-e17b-pgo/metrics.txt /data/tk048/chain96-stand-metrics.txt
rm -rf $SHA
touch /data/tk048/chain96.done
