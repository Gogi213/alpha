#!/bin/bash
# chain82 (TK-048/TK-053 07.10): chain80 (alpha-b18pgoflag, PGO b16t + tbin) + каталог abin-t46m скопирован в /dev/shm. Копия до замера — «подготовка данных», в wall не входит. Защита: файлов < 5000 или объём вне 2–9 ГБ ⇒ ABORT без волны.
# Запуск: systemd-run --unit tk048-chain82 --collect bash /data/tk048/chain82.sh ; выход /data/tk048/chain82.txt, маркер chain82.done.
rm -f /data/tk048/chain82.done /data/tk048/chain82.txt
SHA=/dev/shm/abin-t46m
rm -rf $SHA
t0=$(date +%s)
nice -n 19 ionice -c3 cp -aL /data/tk048/abin-t46m $SHA
n=$(find $SHA -type f | wc -l); mb=$(du -sm $SHA | cut -f1)
{ echo "prep_copy_s $(( $(date +%s)-t0 ))"; echo "shm_df $(df -m /dev/shm | tail -1)"; echo "shm_n_files $n shm_mb $mb"; } >> /data/tk048/chain82.txt
if [ "$n" -lt 5000 ] || [ "$mb" -lt 2000 ] || [ "$mb" -gt 9000 ]; then echo "ABORT копия abin в шм не прошла защиту" >> /data/tk048/chain82.txt; rm -rf $SHA; touch /data/tk048/chain82.done; exit 1; fi
cat > /data/tk048/chain82-inner.sh <<'IN'
#!/bin/bash
for nm in A:alpha-b18pgoflag B:alpha-b18pgoflag; do
tag=${nm%%:*}; bin=${nm#*:}
systemd-run --wait --collect --unit tk048-c82$tag -p CPUQuota=1500% --setenv=ALPHA_APPROACH_BIN_DIR=/dev/shm/abin-t46m --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=PFLIST=/data/tk048/pflist-jf59.txt --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3m.sh bash /data/tk048/tk048-orch-grp16t.sh c82$tag $bin 8 20 > /dev/null 2>&1
{ echo "== $tag $bin shm-abin"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0|read_GB|rchar" /data/tk048/orch-c82$tag.out/metrics.txt; } >> /data/tk048/chain82.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain82-inner.sh > /dev/null 2>&1
rm -rf $SHA
touch /data/tk048/chain82.done
