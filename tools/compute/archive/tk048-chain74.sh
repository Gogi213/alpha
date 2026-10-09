#!/bin/bash
# chain74 (TK-048 07.10): полный янв+фев 59 суток ОДНОЙ очередью grp16m (b14flag, P=20, G=8, abin-t46m), порядок по суткам (своих длительностей нет — честный первый прогон); units -> /data/tk048/orch-c74J59.out/units.txt для QALL повтора.
# Запуск: systemd-run --unit tk048-chain74 --collect bash /data/tk048/chain74.sh ; выход /data/tk048/chain74.txt, маркер chain74.done.
rm -f /data/tk048/chain74.done /data/tk048/chain74.txt
cat > /data/tk048/chain74-inner.sh <<'IN'
#!/bin/bash
nm=J59
systemd-run --wait --collect --unit tk048-c74$nm -p CPUQuota=1500% --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16m.sh c74$nm alpha-b14flag 8 20 > /dev/null 2>&1
{ echo "== $nm"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0" /data/tk048/orch-c74$nm.out/metrics.txt; } >> /data/tk048/chain74.txt
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain74-inner.sh > /dev/null 2>&1
touch /data/tk048/chain74.done
