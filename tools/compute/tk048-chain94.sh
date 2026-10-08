#!/bin/bash
# chain94 (TK-048, гейт C' по Судье 01:13/05:27): ABBA янв+фев 59 суток, A = alpha-b24c0flag (C' выкл), B = alpha-b24cflag (ALPHA_CARRY_EDGE=1);
# обе стороны — боевая связка b23pgoflag на одном бинарнике alpha-b24c-pgo (b23-профиль). Порядок B A A B; gate diff 0 к эталону — в metrics каждого прогона.
# Запуск: systemd-run --unit tk048-chain94 --collect bash /data/tk048/chain94.sh ; выход /data/tk048/chain94.txt, маркер chain94.done.
rm -f /data/tk048/chain94.done /data/tk048/chain94.txt
OUT=/data/tk048/chain94.txt
SHA=/dev/shm/abin-t46m94
rm -rf $SHA
t0=$(date +%s)
nice -n 19 ionice -c3 cp -aL /data/tk048/abin-t46m $SHA
n=$(find $SHA -type f | wc -l); smb=$(du -sm $SHA | cut -f1)
{ echo "prep_copy_s $(( $(date +%s)-t0 ))"; echo "shm_n_files $n shm_mb $smb"; } >> $OUT
if [ "$n" -lt 5000 ] || [ "$smb" -lt 2000 ] || [ "$smb" -gt 9000 ]; then echo "ABORT копия abin в шм не прошла защиту" >> $OUT; rm -rf $SHA; touch /data/tk048/chain94.done; exit 1; fi
sed 's#/data/tk048/tk048-sample.sh#/data/tk048/tk048-sample3.sh#' /data/tk048/tk048-orch-grp16t.sh > /data/tk048/tk048-orch-grp16t-s3e.sh
grep -q tk048-sample3.sh /data/tk048/tk048-orch-grp16t-s3e.sh || { echo "ABORT подмена сэмплера не сработала" >> $OUT; touch /data/tk048/chain94.done; exit 1; }
cat > /data/tk048/chain94-inner.sh <<'IN'
#!/bin/bash
OUT=/data/tk048/chain94.txt
for arm in "c94b alpha-b24cflag" "c94a alpha-b24c0flag" "c94a2 alpha-b24c0flag" "c94b2 alpha-b24cflag"; do set -- $arm
  systemd-run --wait --collect --unit tk048-$1 -p CPUQuota=1500% --setenv=ALPHA_APPROACH_BIN_DIR=/dev/shm/abin-t46m94 --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=PFLIST=/data/tk048/pflist-jf59.txt --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3m.sh bash /data/tk048/tk048-orch-grp16t-s3e.sh $1 $2 8 20 > /dev/null 2>&1
  { echo "== $1 $2"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0|read_GB|rchar|user_s|cpu" /data/tk048/orch-$1.out/metrics.txt; } >> $OUT
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain94-inner.sh > /dev/null 2>&1
rm -rf $SHA
touch /data/tk048/chain94.done
