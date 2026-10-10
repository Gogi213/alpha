#!/usr/bin/env bash
# TK-048: доли стоимости строки (rdtsc): алфа-e20-rdtsc VPS → сервер счёта, стенд d15 через демон (benchrun stand)
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); V=root@13.140.29.171; C=root@89.163.242.211
n=alpha-e20-rdtsc; B=/opt/alpha-compute/bin
ssh "${K[@]}" $V "test -e /opt/alpha-compute/tk048-e20.done" || { echo "сборка не готова"; exit 1; }
m=$(ssh "${K[@]}" $V "md5sum < $B/$n" | cut -d' ' -f1)
ssh "${K[@]}" $V "cat $B/$n" | ssh "${K[@]}" $C "cat > $B/$n.new && chmod +x $B/$n.new"
m2=$(ssh "${K[@]}" $C "md5sum < $B/$n.new" | cut -d' ' -f1)
[ "$m" = "$m2" ] || { echo "md5 не сошёлся"; exit 1; }
ssh "${K[@]}" $C "mv $B/$n.new $B/$n; mkdir -p /data/tk048/rdtsc-d15; rm -f /data/tk048/rdtsc-d15/done
cat > /data/tk048/rdtsc-d15/run.sh <<'EOS'
#!/bin/bash
cd /data/tk051
/data/benchrun.sh stand bash stand.sh alpha-e20-rdtsc d15 ALPHA_SKIP_SAME=1 ALPHA_EVENT_STEPS=1 ALPHA_ATTEMPT_STATS=1 > /data/tk048/rdtsc-d15/out.txt 2>&1
touch /data/tk048/rdtsc-d15/done
EOS
systemctl reset-failed tk048-rdtsc 2>/dev/null; systemd-run --quiet --unit tk048-rdtsc --collect bash /data/tk048/rdtsc-d15/run.sh && echo юнит tk048-rdtsc запущен"
