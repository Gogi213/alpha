#!/bin/bash
# chain80 = К-5в (TK-048 09.10, Судья 08:56): как chain79, но читатель только по файлам D, которые единица декодирует целиком (HINT=r5-hint.txt, 438 файлов, 9,75 ГБ, из ev_decode волны A), D_ONLY=1. A = PREFETCH=5, B = READER5=1. Останов: B1 > 190 с.
# Запуск: systemd-run --unit tk048-chain80 --collect bash /data/tk048/chain80.sh ; выход /data/tk048/chain80.txt, маркер chain80.done.
rm -f /data/tk048/chain80.done /data/tk048/chain80.txt
cat > /data/tk048/chain80-inner.sh <<'IN'
#!/bin/bash
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for r in A1:0 B1:1 A2:0 B2:1; do
  nm=${r%%:*}; rd=${r##*:}; n=120; pf=5; [ $rd = 1 ] && pf=0
  systemd-run --wait --collect --unit tk048-c80$nm -p CPUQuota=1500% --setenv=QTOP_FROM=/data/tk048/units-q15b14ac.txt --setenv=QTOP_N=$n --setenv=HOIST_PRED=/data/tk048/pred-q15.txt --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=HOIST_S=40 --setenv=WARM_BG=1 --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=$pf --setenv=READER5=$rd --setenv=D_ONLY=1 --setenv=HINT=/data/tk048/r5-hint.txt --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16q.sh c80$nm alpha-b14flag $D15 8 20 > /dev/null 2>&1
  { echo "== $nm N=$n"; head -3 /data/tk048/orch-c80$nm/queue.txt | sed "s/^/queue_head /"; [ -e /data/tk048/orch-c80$nm.out/r5.err ] && { echo r5_tail; tail -3 /data/tk048/orch-c80$nm.out/r5.err; }; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity" /data/tk048/orch-c80$nm.out/metrics.txt; } >> /data/tk048/chain80.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain80-inner.sh > /dev/null 2>&1
touch /data/tk048/chain80.done
