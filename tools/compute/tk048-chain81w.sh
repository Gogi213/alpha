#!/bin/bash
# chain81w (К-9, TK-048): ОДИН холодный прогон Y12 = 241 + 2650 добавок вразброс (A 1330 + B 1320), волна 01–15.01, конфиг chain80-A.
# Пишет chain81w.txt; память юнита и /dev/shm — chain81w-mem.txt (раз в 5 с: t MemoryCurrent_B shm_used_KB). Прогноз ДО прогона: 118,5+0,387·2891 ≈ 1237 с, ×2,05 к X1.
rm -f /data/tk048/chain81w.done /data/tk048/chain81w.txt /data/tk048/chain81w-mem.txt
cat > /data/tk048/chain81w-inner.sh <<"IN"
#!/bin/bash
D15=$(seq -s, -f "2026-01-%02g" 1 15)
( while sleep 5; do echo "$(date +%s) $(systemctl show tk048-c81wY12 -p MemoryCurrent --value 2>/dev/null) $(df --output=used /dev/shm | tail -1)"; done > /data/tk048/chain81w-mem.txt ) & MS=$!
systemd-run --wait --collect --unit tk048-c81wY12 -p CPUQuota=1500% -p MemoryAccounting=yes --setenv=JOB3=/data/tk048/tk048-orch-job3x.sh --setenv=JDIR=/data/tk048/k8w/x12 --setenv=QTOP_FROM=/data/tk048/units-q15b14ac.txt --setenv=QTOP_N=120 --setenv=HOIST_PRED=/data/tk048/pred-q15.txt --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=HOIST_S=40 --setenv=WARM_BG=1 --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=5 --setenv=READER5=0 --setenv=D_ONLY=1 --setenv=HINT=/data/tk048/r5-hint.txt --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16qx.sh c81wY12 alpha-b14flag $D15 8 20 > /dev/null 2>&1
kill $MS 2>/dev/null
{ echo "== Y12 cells x12"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|cpu|user|maxrss" /data/tk048/orch-c81wY12.out/metrics.txt; cat /data/tk048/orch-c81wY12.out/fail.txt 2>/dev/null; awk '{if($2>m)m=$2; if($3>s)s=$3} END{print "mem_unit_peak_MB",int(m/1048576),"shm_used_peak_MB",int(s/1024)}' /data/tk048/chain81w-mem.txt; } >> /data/tk048/chain81w.txt
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain81w-inner.sh > /dev/null 2>&1
touch /data/tk048/chain81w.done
