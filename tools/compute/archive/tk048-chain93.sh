#!/bin/bash
# chain93 (TK-048 A/B I, повтор): как chain92, но бинарник alpha-b23pgoflag = база chain91 (634,6 с); chain92 шёл на b19r1flag — несопоставим.
# Запуск: systemd-run --unit tk048-chain93 --collect bash /data/tk048/chain93.sh ; выход /data/tk048/chain93.txt, маркер chain93.done.
rm -f /data/tk048/chain93.done /data/tk048/chain93.txt
OUT=/data/tk048/chain93.txt
left=0; while read -r sy d; do case ${d:5:2} in 01) MON=jan;; 02) MON=feb;; *) continue;; esac
  for f in /data/tk046/$MON/home/alpha/epochs/e-$MON/study/root-$d/$sy-$d.binlog; do [ -e "$f" ] && case "$(readlink -f "$f")" in /alpha-sda/*) ;; *) left=$((left+1));; esac; done
done < /data/tk048/pflist-jf59.txt
echo "binlogs_not_on_sda $left" >> $OUT
SHA=/dev/shm/abin-t46m93
rm -rf $SHA
t0=$(date +%s)
nice -n 19 ionice -c3 cp -aL /data/tk048/abin-t46m $SHA
n=$(find $SHA -type f | wc -l); smb=$(du -sm $SHA | cut -f1)
{ echo "prep_copy_s $(( $(date +%s)-t0 ))"; echo "shm_df $(df -m /dev/shm | tail -1)"; echo "shm_n_files $n shm_mb $smb"; } >> $OUT
if [ "$n" -lt 5000 ] || [ "$smb" -lt 2000 ] || [ "$smb" -gt 9000 ]; then echo "ABORT копия abin в шм не прошла защиту" >> $OUT; rm -rf $SHA; touch /data/tk048/chain93.done; exit 1; fi
sed 's#/data/tk048/tk048-sample.sh#/data/tk048/tk048-sample3.sh#' /data/tk048/tk048-orch-grp16t.sh > /data/tk048/tk048-orch-grp16t-s3d.sh
sed -i 's#mkdir $M/bin;#[ -n "$CARRY_SDA" ] \&\& ln -sfn $CARRY_SDA/$MON/root $M/root; mkdir $M/bin;#' /data/tk048/tk048-orch-grp16t-s3d.sh; grep -q CARRY_SDA /data/tk048/tk048-orch-grp16t-s3d.sh || { echo "ABORT патч carry-sda не сработал" >> $OUT; touch /data/tk048/chain93.done; exit 1; }
grep -q tk048-sample3.sh /data/tk048/tk048-orch-grp16t-s3d.sh || { echo "ABORT подмена сэмплера не сработала" >> $OUT; touch /data/tk048/chain93.done; exit 1; }
cat > /data/tk048/chain93-inner.sh <<'IN'
#!/bin/bash
OUT=/data/tk048/chain93.txt; FL=/data/tk048/frozen93.list; : > $FL
pick() { true; }
snap() { for u in $(cat $FL); do echo "$1 $u $(tr -s '[:space:]' ' ' < /sys/fs/cgroup/system.slice/$u/io.stat 2>/dev/null)"; done >> $OUT; }
freeze_new() { for u in $(pick); do grep -qx "$u" $FL || { systemctl freeze "$u" && echo "$u" >> $FL; }; done; }
thaw_all() { for u in $(cat $FL); do systemctl thaw "$u" 2>/dev/null; done; }
systemd-run --on-active=1800 --unit tk048-thaw93 --collect bash -c "for u in \$(cat $FL); do systemctl thaw \$u; done" > /dev/null 2>&1
freeze_new
echo "frozen_t0 $(date +%T) n $(wc -l < $FL)" >> $OUT; snap io_before
( while true; do sleep 3; freeze_new; done ) & LP=$!
trap 'kill $LP 2>/dev/null; snap io_after; thaw_all; echo "thawed $(date +%T) n $(wc -l < $FL)" >> $OUT; systemctl stop tk048-thaw93.timer 2>/dev/null' EXIT
systemd-run --wait --collect --unit tk048-c93 -p CPUQuota=1500% --setenv=CARRY_SDA=/data/tk048/carry-sda --setenv=ALPHA_APPROACH_BIN_DIR=/dev/shm/abin-t46m93 --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=PFLIST=/data/tk048/pflist-jf59.txt --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3m.sh bash /data/tk048/tk048-orch-grp16t-s3d.sh c93 alpha-b23pgoflag 8 20 > /dev/null 2>&1
{ echo "== c93 alpha-b23pgoflag shm-abin + sample3 + TK-064 G1 b19r1 flag off, no freeze"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0|read_GB|rchar" /data/tk048/orch-c93.out/metrics.txt; } >> /data/tk048/chain93.txt
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain93-inner.sh > /dev/null 2>&1
rm -rf $SHA
touch /data/tk048/chain93.done
