#!/bin/bash
# chain79 = К-5 (TK-048 09.10, Судья 08:05): две пары AB холодной волны 01–15.01: A = текущая оркестровка (PREFETCH=5), B = READER5=1 (tk048-r5.py, PREFETCH=0); chain71 (QTOP исправлен: chr(10), TK-048 07.10; рычаг 1 TK-053, В-185): холодная волна 01–15.01, конфиг chain48 ac (b14flag, P=20, grp16, HOIST+WARM_BG, abin-t46) + очередь по измеренной стоимости (QTOP_FROM = units ac). Порядок: Q6 (топ-6), QALL (полный LPT), Q6b (повтор). Эталон 215,5 с.
# Запуск: systemd-run --unit tk048-chain79 --collect bash /data/tk048/chain79.sh ; выход /data/tk048/chain79.txt, маркер chain79.done.
rm -f /data/tk048/chain79.done /data/tk048/chain79.txt
cat > /data/tk048/chain79-inner.sh <<'IN'
#!/bin/bash
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for r in A1:0 B1:1 A2:0 B2:1; do
  nm=${r%%:*}; rd=${r##*:}; n=120; pf=5; [ $rd = 1 ] && pf=0
  systemd-run --wait --collect --unit tk048-c79$nm -p CPUQuota=1500% --setenv=QTOP_FROM=/data/tk048/units-q15b14ac.txt --setenv=QTOP_N=$n --setenv=HOIST_PRED=/data/tk048/pred-q15.txt --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=HOIST_S=40 --setenv=WARM_BG=1 --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=$pf --setenv=READER5=$rd --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16q.sh c79$nm alpha-b14flag $D15 8 20 > /dev/null 2>&1
  { echo "== $nm N=$n"; head -3 /data/tk048/orch-c79$nm/queue.txt | sed "s/^/queue_head /"; [ -e /data/tk048/orch-c79$nm.out/r5.err ] && { echo r5_tail; tail -3 /data/tk048/orch-c79$nm.out/r5.err; }; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity" /data/tk048/orch-c79$nm.out/metrics.txt; } >> /data/tk048/chain79.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain79-inner.sh > /dev/null 2>&1
touch /data/tk048/chain79.done
