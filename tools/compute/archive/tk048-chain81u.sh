#!/bin/bash
# chain81u (К-8 шаг 1, TK-048): холодная волна 01–15.01 при x1/x2/x4 клеток (JDIR=k8/xN), конфиг chain80-A (PREFETCH=5, D_ONLY, QTOP). Гейт — по телу (без 1-й строки-шапки, в ней список форм). Выход chain81u.txt, rounds-count в k8/.
rm -f /data/tk048/chain81u.done /data/tk048/chain81u.txt
cat > /data/tk048/chain81u-inner.sh <<"IN"
#!/bin/bash
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for k in 2a 2b; do
  nm=Y$k
  systemd-run --wait --collect --unit tk048-c81u$nm -p CPUQuota=1500% --setenv=JOB3=/data/tk048/tk048-orch-job3x.sh --setenv=JDIR=/data/tk048/k8u/x$k --setenv=QTOP_FROM=/data/tk048/units-q15b14ac.txt --setenv=QTOP_N=120 --setenv=HOIST_PRED=/data/tk048/pred-q15.txt --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=HOIST_S=40 --setenv=WARM_BG=1 --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=5 --setenv=READER5=0 --setenv=D_ONLY=1 --setenv=HINT=/data/tk048/r5-hint.txt --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16qx.sh c81u$nm alpha-b14flag $D15 8 20 > /dev/null 2>&1
  { echo "== $nm cells x$k"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|cpu|user" /data/tk048/orch-c81u$nm.out/metrics.txt; cat /data/tk048/orch-c81u$nm.out/fail.txt 2>/dev/null; } >> /data/tk048/chain81u.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain81u-inner.sh > /dev/null 2>&1
touch /data/tk048/chain81u.done
