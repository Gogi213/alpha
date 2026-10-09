#!/bin/bash
# chain73 (TK-048 07.10, вторая половина вехи ×5): полный январь 01–31 одной волной grp16 (b14flag, P=20, G=8), порядок по суткам (без QTOP — своих длительностей ещё нет); units -> /data/tk048/orch-c73J31.out/units.txt для QALL следующей волны. Эталон 15 суток 215,5 с.
# Запуск: systemd-run --unit tk048-chain73 --collect bash /data/tk048/chain73.sh ; выход /data/tk048/chain73.txt, маркер chain73.done.
rm -f /data/tk048/chain73.done /data/tk048/chain73.txt
cat > /data/tk048/chain73-inner.sh <<'IN'
#!/bin/bash
D=$(seq -s, -f "2026-01-%02g" 1 31)
nm=J31
systemd-run --wait --collect --unit tk048-c73$nm -p CPUQuota=1500% --setenv=MON=jan --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16q.sh c73$nm alpha-b14flag $D 8 20 > /dev/null 2>&1
{ echo "== $nm"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0" /data/tk048/orch-c73$nm.out/metrics.txt; } >> /data/tk048/chain73.txt
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain73-inner.sh > /dev/null 2>&1
touch /data/tk048/chain73.done
