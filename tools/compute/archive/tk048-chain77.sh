#!/bin/bash
# chain77 (TK-048 07.10, рычаг touches .abin): три волны 59 суток янв+фев одной очередью grp16t (P=20, G=8, abin-t46m, PFLIST, как chain76):
# W — прогрев/построение touches .abin (alpha-b16tbinflag, не в зачёт), T — замерочная с флагом, K — контроль того же бинарника без флага (alpha-b16tbinoff).
# Запуск: systemd-run --unit tk048-chain77 --collect bash /data/tk048/chain77.sh ; выход /data/tk048/chain77.txt, маркер chain77.done.
rm -f /data/tk048/chain77.done /data/tk048/chain77.txt
cat > /data/tk048/chain77-inner.sh <<'IN'
#!/bin/bash
for nm in W:alpha-b16tbinflag T:alpha-b16tbinflag K:alpha-b16tbinoff; do
tag=${nm%%:*}; bin=${nm#*:}
systemd-run --wait --collect --unit tk048-c77$tag -p CPUQuota=1500% --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=PFLIST=/data/tk048/pflist-jf59.txt --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3m.sh bash /data/tk048/tk048-orch-grp16t.sh c77$tag $bin 8 20 > /dev/null 2>&1
{ echo "== $tag $bin"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0|read_GB|rchar" /data/tk048/orch-c77$tag.out/metrics.txt; } >> /data/tk048/chain77.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain77-inner.sh > /dev/null 2>&1
touch /data/tk048/chain77.done
