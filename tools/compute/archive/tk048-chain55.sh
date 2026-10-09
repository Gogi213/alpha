#!/bin/bash
# chain55 (CEO 06.10 16:40 п.4): тёплый обратный проход. В одном окне wave: проход A (холодный, прямой порядок суток, как chain52/53)
# и сразу проход B (NODROP=1, REVDAYS=1: кэш страниц от A, сутки с конца). Метрики: /data/tk048/{wf,wr}-b14m.out/metrics.txt.
rm -f /data/tk048/chain55.done
cat > /data/tk048/chain55-inner.sh <<'IN'
#!/bin/bash
ENVV="--setenv=PREWARM_SMALL=1 --setenv=ALPHA_SKIP_SAME=1 --setenv=EVENTS_WIDE=1 --setenv=ALPHA_APPROACH_BIN_DIR=/data/tk048/abin-t46m"
systemd-run --wait --collect --unit tk048-wf-b14m -p CPUQuota=1500% $ENVV bash /data/tk048/tk048-orch-grpr.sh wf-b14m alpha-b14flag 8 15 > /dev/null 2>&1
systemd-run --wait --collect --unit tk048-wr-b14m -p CPUQuota=1500% $ENVV --setenv=NODROP=1 --setenv=REVDAYS=1 bash /data/tk048/tk048-orch-grpr.sh wr-b14m alpha-b14flag 8 15 > /dev/null 2>&1
IN
chmod +x /data/tk048/chain55-inner.sh
/data/tk052/benchrun2.sh wave bash /data/tk048/chain55-inner.sh > /dev/null 2>&1
touch /data/tk048/chain55.done
