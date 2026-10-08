#!/bin/bash
# chain78 (TK-048 07.10): одна замерочная волна 59 суток янв+фев с touches .abin на PGO-сборке alpha-b16tbin-pgo (профиль b15pyr4) — против chain78 T 773,3 с (без PGO) и chain76 758,4 с (b14 PGO, без tbin).
# Запуск: systemd-run --unit tk048-chain78 --collect bash /data/tk048/chain78.sh ; выход /data/tk048/chain78.txt, маркер chain78.done.
rm -f /data/tk048/chain78.done /data/tk048/chain78.txt
cat > /data/tk048/chain78-inner.sh <<'IN'
#!/bin/bash
for nm in T:alpha-b16tbinpgoflag; do
tag=${nm%%:*}; bin=${nm#*:}
systemd-run --wait --collect --unit tk048-c78$tag -p CPUQuota=1500% --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=PFLIST=/data/tk048/pflist-jf59.txt --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3m.sh bash /data/tk048/tk048-orch-grp16t.sh c78$tag $bin 8 20 > /dev/null 2>&1
{ echo "== $tag $bin"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0|read_GB|rchar" /data/tk048/orch-c78$tag.out/metrics.txt; } >> /data/tk048/chain78.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain78-inner.sh > /dev/null 2>&1
touch /data/tk048/chain78.done
