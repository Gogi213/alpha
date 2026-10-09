#!/bin/bash
# chain70 (рычаги 2+3 TK-053, В-185): холодная волна 01–15.01, конфиг chain48 ac (b14flag, P=20, grp16, HOIST+WARM_BG, abin-t46) + блок старта (START_BLOCK) + параллельный хвост B (job3q). Порядок: S2B3 (оба), S2B3b (повтор), B3 (только хвост B). Эталон 215,5 с.
# Запуск: systemd-run --unit tk048-chain70 --collect bash /data/tk048/chain70.sh ; выход /data/tk048/chain70.txt, маркер chain70.done.
rm -f /data/tk048/chain70.done /data/tk048/chain70.txt
cat > /data/tk048/chain70-inner.sh <<'IN'
#!/bin/bash
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for r in S2B3:1 S2B3b:1 B3:0; do
  nm=${r%%:*}; sb=${r##*:}
  systemd-run --wait --collect --unit tk048-c70$nm -p CPUQuota=1500% --setenv=START_BLOCK=$sb --setenv=JOB3=/data/tk048/tk048-orch-job3q.sh --setenv=HOIST_PRED=/data/tk048/pred-q15.txt --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=HOIST_S=40 --setenv=WARM_BG=1 --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16q.sh c70$nm alpha-b14flag $D15 8 20 > /dev/null 2>&1
  { echo "== $nm START_BLOCK=$sb"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity" /data/tk048/orch-c70$nm.out/metrics.txt; echo "first_unit_after_t0 $(awk 'NR==1{t=$1} END{}' /data/tk048/orch-c70$nm.out/samples.tsv) $(sort -k3 -n /data/tk048/orch-c70$nm.out/units.txt | head -1)"; } >> /data/tk048/chain70.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain70-inner.sh > /dev/null 2>&1
touch /data/tk048/chain70.done
