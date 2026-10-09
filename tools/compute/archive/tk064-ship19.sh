#!/usr/bin/env bash
# tk064-ship19.sh: b19head/b19r1 с VPS на сервер счёта (md5), обёртка alpha-b19r1flag, скрипты chain6/gridperf/sig/chain91, запуск юнита tk064-chain6 (маркер /data/tk064/chain6.done, лог chain6.log)
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); V=root@13.140.29.171; C=root@89.163.242.211
D=$(cd "$(dirname "$0")" && pwd)
for n in b19head b19r1; do
  ssh "${K[@]}" $V "test -e /opt/alpha-compute/tk051-$n.done" || { echo "сборка $n не готова"; exit 1; }
  m=$(ssh "${K[@]}" $V "md5sum < /opt/alpha-compute/bin/$n" | cut -d' ' -f1)
  ssh "${K[@]}" $V "cat /opt/alpha-compute/bin/$n" | ssh "${K[@]}" $C "cat > /opt/alpha-compute/bin/$n.new && chmod +x /opt/alpha-compute/bin/$n.new"
  m2=$(ssh "${K[@]}" $C "md5sum < /opt/alpha-compute/bin/$n.new" | cut -d' ' -f1)
  [ "$m" = "$m2" ] || { echo "md5 не сошёлся $n"; exit 1; }
  ssh "${K[@]}" $C "mv /opt/alpha-compute/bin/$n.new /opt/alpha-compute/bin/$n"; echo "$n $m ok"
done
scp -q "${K[@]}" "$D/alpha-b19r1flag" $C:/opt/alpha-compute/bin/alpha-b19r1flag
scp -q "${K[@]}" "$D/tk064-gridperf.sh" $C:/data/tk064/gridperf.sh
scp -q "${K[@]}" "$D/tk064-sig.sh" $C:/data/tk064/tk064-sig.sh
scp -q "${K[@]}" "$D/tk048-chain91.sh" $C:/data/tk048/chain91.sh
scp -q "${K[@]}" "$D/tk064-chain6.sh" $C:/data/tk064/chain6.sh
ssh "${K[@]}" $C "chmod +x /opt/alpha-compute/bin/alpha-b19r1flag /data/tk064/gridperf.sh /data/tk064/tk064-sig.sh /data/tk048/chain91.sh /data/tk064/chain6.sh; systemctl reset-failed tk064-chain6 2>/dev/null; systemd-run --quiet --unit tk064-chain6 --collect bash /data/tk064/chain6.sh && echo юнит tk064-chain6 запущен"
