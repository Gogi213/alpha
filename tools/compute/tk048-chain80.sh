#!/bin/bash
# chain80 (TK-048 07.10): как chain78, но PGO со свежим профилем b16t (обучение на сборке b16 с tbin) — alpha-b16t-pgo; против chain78 742,4 с.
# Запуск: systemd-run --unit tk048-chain80 --collect bash /data/tk048/chain80.sh ; выход /data/tk048/chain80.txt, маркер chain80.done.
rm -f /data/tk048/chain80.done /data/tk048/chain80.txt
cat > /data/tk048/chain80-inner.sh <<'IN'
#!/bin/bash
for nm in T:alpha-b16tpgoflag; do
tag=${nm%%:*}; bin=${nm#*:}
systemd-run --wait --collect --unit tk048-c80$tag -p CPUQuota=1500% --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m --setenv=WARM_BG=1 --setenv=PREFETCH=5 --setenv=WARM_ABIN=1 --setenv=WARM_NOCSV=1 --setenv=PREWARM_SMALL=1 --setenv=PFLIST=/data/tk048/pflist-jf59.txt --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=JOB3=/data/tk048/tk048-orch-job3m.sh bash /data/tk048/tk048-orch-grp16t.sh c80$tag $bin 8 20 > /dev/null 2>&1
{ echo "== $tag $bin"; grep -E "wall_s|iowait|core_util|disk_MBps|gate|interference|tail_s|validity|NODROP|cached0|read_GB|rchar" /data/tk048/orch-c80$tag.out/metrics.txt; } >> /data/tk048/chain80.txt
done
IN
/data/tk052/benchrun2.sh wave bash /data/tk048/chain80-inner.sh > /dev/null 2>&1
touch /data/tk048/chain80.done
