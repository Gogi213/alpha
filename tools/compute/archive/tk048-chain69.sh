#!/bin/bash
# chain69 (рычаг 1 TK-053, В-185): холодная волна 01–15.01, конфиг chain48 ac (b14flag, P=20, grp16, HOIST+WARM_BG, abin-t46) + очередь по измеренной стоимости (QTOP_FROM = units ac). Порядок: Q6 (топ-6), QALL (полный LPT), Q6b (повтор). Эталон 215,5 с.
# Запуск: systemd-run --unit tk048-chain69 --collect bash /data/tk048/chain69.sh ; выход /data/tk048/chain69.txt, маркер chain69.done.
rm -f /data/tk048/chain69.done /data/tk048/chain69.txt
cat > /data/tk048/chain69-inner.sh <<'IN'
#!/bin/bash
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for r in Q6:6 QALL:120 Q6b:6; do
  nm=${r%%:*}; n=${r##*:}
  systemd-run --wait --collect --unit tk048-c69$nm -p CPUQuota=1500% --setenv=QTOP_FROM=/data/tk048/units-q15b14ac.txt --setenv=QTOP_N=$n --setenv=HOIST_PRED=/data/tk048/pred-q15.txt --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=HOIST_S=40 --setenv=WARM_BG=1 --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16q.sh c69$nm alpha-b14flag $D15 8 20 > /dev/null 2>&1
  { echo "== $nm N=$n"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity" /data/tk048/orch-c69$nm.out/metrics.txt; } >> /data/tk048/chain69.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain69-inner.sh > /dev/null 2>&1
touch /data/tk048/chain69.done
