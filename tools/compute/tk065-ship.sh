#!/usr/bin/env bash
# tk065-ship.sh: бинари головы и кандидата R2 с VPS на сервер счёта (md5), обёртка alpha-93287fd-flag (env как у alpha-b15pyr4pgoflag), скрипты, запуск юнита tk065-gchain.
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes); V=root@13.140.29.171; C=root@89.163.242.211
D=$(cd "$(dirname "$0")" && pwd)
for n in alpha-b976c31-v3 alpha-93287fd-v3; do
  m=$(ssh "${K[@]}" $V "md5sum < /opt/alpha-compute/bin/$n" | cut -d' ' -f1)
  ssh "${K[@]}" $V "cat /opt/alpha-compute/bin/$n" | ssh "${K[@]}" $C "cat > /opt/alpha-compute/bin/$n.new && chmod +x /opt/alpha-compute/bin/$n.new"
  m2=$(ssh "${K[@]}" $C "md5sum < /opt/alpha-compute/bin/$n.new" | cut -d' ' -f1)
  [ "$m" = "$m2" ] || { echo "md5 не сошёлся $n"; exit 1; }
  ssh "${K[@]}" $C "mv /opt/alpha-compute/bin/$n.new /opt/alpha-compute/bin/$n"; echo "$n $m ok"
done
ssh "${K[@]}" $C "sed 's#/opt/alpha-compute/bin/alpha-b15pyr4-pgo#/opt/alpha-compute/bin/alpha-93287fd-v3#' /opt/alpha-compute/bin/alpha-b15pyr4pgoflag > /opt/alpha-compute/bin/alpha-93287fd-flag; chmod +x /opt/alpha-compute/bin/alpha-93287fd-flag; cat /opt/alpha-compute/bin/alpha-93287fd-flag | tail -2"
scp -q "${K[@]}" "$D/tk064-gridperf.sh" $C:/data/tk065/gridperf.sh
scp -q "${K[@]}" "$D/tk065-g1-day.sh" $C:/data/tk065/g1-day.sh
scp -q "${K[@]}" "$D/tk065-gchain.sh" $C:/data/tk065/gchain.sh
ssh "${K[@]}" $C "chmod +x /data/tk065/gridperf.sh /data/tk065/g1-day.sh /data/tk065/gchain.sh; systemctl reset-failed tk065-gchain 2>/dev/null; systemd-run --quiet --unit tk065-gchain --collect bash /data/tk065/gchain.sh && echo юнит tk065-gchain запущен"
