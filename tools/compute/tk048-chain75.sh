#!/bin/bash
# chain75 (TK-048 07.10): полный янв+фев 59 суток ОДНОЙ очередью grp16m (b14flag, P=20, G=8, abin-t46m), порядок по суткам (своих длительностей нет — честный первый прогон); units -> /data/tk048/orch-c75J59.out/units.txt для QALL повтора.
# Запуск: systemd-run --unit tk048-chain75 --collect bash /data/tk048/chain75.sh ; выход /data/tk048/chain75.txt, маркер chain75.done.
rm -f /data/tk048/chain75.done /data/tk048/chain75.txt
cat > /data/tk048/chain75-inner.sh <<'IN'
#!/bin/bash
nm=J59
systemd-run --wait --collect --unit tk048-c75$nm -p CPUQuota=1500% --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3m.sh bash /data/tk048/tk048-orch-grp16m.sh c75$nm alpha-b14flag 8 20 > /dev/null 2>&1
{ echo "== $nm"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0" /data/tk048/orch-c75$nm.out/metrics.txt; } >> /data/tk048/chain75.txt
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain75-inner.sh > /dev/null 2>&1
touch /data/tk048/chain75.done
