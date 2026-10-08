#!/bin/bash
# TK-048: доставить march-ab4.sh на сервер счёта и запустить юнитом через benchrun2 (как tk048-marab)
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes)
D="$(cd "$(dirname "$0")" && pwd)"
cat "$D/tk048-march-ab4.sh" | ssh "${K[@]}" root@89.163.242.211 "cat > /data/tk048/march-ab4.sh && chmod +x /data/tk048/march-ab4.sh && systemd-run --unit tk048-marab4 --collect /data/tk052/benchrun2.sh stand bash /data/tk048/march-ab4.sh alpha-b20-pgo alpha-b23-pgo-f1"
