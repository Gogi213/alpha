#!/bin/bash
# chain72 (проверка Судьи TK-048, 07.10): QALL, база (без QTOP), QALL повтор, NB (QALL по длительностям соседних суток — вне выборки). Эталон 215,5 с, цель ≤ 205.
# Запуск: systemd-run --unit tk048-chain72 --collect bash /data/tk048/chain72.sh ; выход /data/tk048/chain72.txt, маркер chain72.done. Нужен /data/tk048/units-nb.txt.
rm -f /data/tk048/chain72.done /data/tk048/chain72.txt
cat > /data/tk048/chain72-inner.sh <<'IN'
#!/bin/bash
D15=$(seq -s, -f "2026-01-%02g" 1 15)
for r in QALL:units-q15b14ac.txt BASE: QALLb:units-q15b14ac.txt NB:units-nb.txt; do
  nm=${r%%:*}; src=${r##*:}
  if [ -n "$src" ]; then QE="--setenv=QTOP_FROM=/data/tk048/$src --setenv=QTOP_N=120"; else QE=""; fi
  systemd-run --wait --collect --unit tk048-c72$nm -p CPUQuota=1500% $QE --setenv=HOIST_PRED=/data/tk048/pred-q15.txt --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46 --setenv=HOIST_S=40 --setenv=WARM_BG=1 --setenv=PFLIST=/data/tk048/pflist-q15.txt --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 bash /data/tk048/tk048-orch-grp16q.sh c72$nm alpha-b14flag $D15 8 20 > /dev/null 2>&1
  { echo "== $nm src=$src"; head -3 /data/tk048/orch-c72$nm/queue.txt | sed "s/^/queue_head /"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity" /data/tk048/orch-c72$nm.out/metrics.txt; } >> /data/tk048/chain72.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain72-inner.sh > /dev/null 2>&1
touch /data/tk048/chain72.done
